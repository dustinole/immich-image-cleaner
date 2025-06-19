from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import requests
import sqlite3
import os
import json
import logging
from datetime import datetime
import threading
import time
import re
from PIL import Image
from PIL.ExifTags import TAGS
import hashlib

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/cleaner.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'immich-cleaner-secret-2024')
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
cleaner_engine = None
analysis_running = False

class ImmichImageCleaner:
    def __init__(self, immich_url, api_key):
        self.immich_url = immich_url.rstrip('/')
        self.api_key = api_key
        self.headers = {'X-Api-Key': api_key}
        self.db_path = '/app/data/cleaner_results.db'
        self.ensure_data_directory()
        self.init_database()

    def ensure_data_directory(self):
        os.makedirs('/app/data', exist_ok=True)
        os.makedirs('/app/logs', exist_ok=True)

    def init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id TEXT PRIMARY KEY,
                    filename TEXT,
                    file_path TEXT,
                    category TEXT,
                    confidence_score REAL,
                    reasons TEXT,
                    file_size INTEGER,
                    created_date TEXT,
                    marked_for_deletion BOOLEAN DEFAULT 0,
                    analysis_date TEXT
                )
            ''')
            conn.commit()

    def test_connection(self):
        try:
            if not self.immich_url.endswith('/api'):
                test_url = f"{self.immich_url}/api/search/metadata"
            else:
                test_url = f"{self.immich_url}/search/metadata"
            response = requests.post(test_url, headers=self.headers, json={}, timeout=10)
            logger.info(f"Testing connection to: {test_url}")
            logger.info(f"Response status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if 'assets' in data:
                    return True, "Connection successful"
                else:
                    return False, f"Unexpected response structure: {response.text[:200]}"
            else:
                return False, f"HTTP {response.status_code}: {response.text}"
        except requests.exceptions.RequestException as e:
            logger.error(f"Connection test failed: {str(e)}")
            return False, f"Connection failed: {str(e)}"

    def get_all_assets(self):
        try:
            if not self.immich_url.endswith('/api'):
                url = f"{self.immich_url}/api/search/metadata"
            else:
                url = f"{self.immich_url}/search/metadata"

            all_assets = []
            cursor = None

            while True:
                payload = {"cursor": cursor} if cursor else {}
                response = requests.post(url, headers=self.headers, json=payload, timeout=30)
                if response.status_code != 200:
                    logger.error(f"Failed to fetch assets: HTTP {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    break
                data = response.json()
                if 'assets' not in data or 'items' not in data['assets']:
                    logger.error(f"Unexpected response structure: {list(data.keys())}")
                    break
                items = data['assets']['items']
                logger.info(f"Fetched {len(items)} assets in this page")
                all_assets.extend(items)
                if data['assets'].get('hasNextPage') and data['assets'].get('nextCursor'):
                    cursor = data['assets']['nextCursor']
                else:
                    break
            logger.info(f"Successfully fetched {len(all_assets)} total assets")
            return all_assets
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching assets: {str(e)}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response: {str(e)}")
            logger.error(f"Response content: {response.text[:500]}...")
            return []

    def analyze_asset(self, asset):
        reasons, confidence, category = [], 0.0, "unknown"
        filename = asset.get('originalFileName', '').lower()
        file_path = asset.get('originalPath', '')
        if self.is_screenshot(asset, filename):
            category, confidence = "screenshot", 0.8
            reasons.append("Screenshot detected")
        elif self.is_web_cache(asset, filename, file_path):
            category, confidence = "web_cache", 0.7
            reasons.append("Web cache/thumbnail detected")
        elif self.is_recovery_artifact(asset, filename, file_path):
            category, confidence = "recovery_artifact", 0.9
            reasons.append("Data recovery artifact")
        elif self.is_likely_duplicate(asset, filename):
            category, confidence = "duplicate", 0.6
            reasons.append("Potential duplicate")
        return {
            'category': category,
            'confidence': min(confidence, 1.0),
            'reasons': '; '.join(reasons)
        }

    def is_screenshot(self, asset, filename):
        patterns = [r'screenshot', r'screen_shot', r'screen shot', r'img_\d+', r'image_\d+',
                    r'photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}', r'screenshot_\d+', r'capture_\d+']
        if any(re.search(p, filename, re.IGNORECASE) for p in patterns):
            return True
        exif = asset.get('exifInfo', {})
        if exif.get('imageWidth') and exif.get('imageHeight'):
            w, h = exif['imageWidth'], exif['imageHeight']
            return (w, h) in [(1080, 1920), (1440, 2560), (1125, 2436), (828, 1792), (750, 1334), (1080, 2340)]
        return False

    def is_web_cache(self, asset, filename, file_path):
        indicators = ['cache', 'thumb', 'thumbnail', 'temp', 'tmp', 'preview', 'avatar', 'profile', 'social',
                      'facebook', 'instagram', 'twitter', 'whatsapp']
        return any(x in filename or x in file_path.lower() for x in indicators)

    def is_recovery_artifact(self, asset, filename, file_path):
        patterns = [r'recovered', r'restore', r'backup', r'file_\d+', r'img_\d{4}', r'dsc_\d+',
                    r'copy_of', r'duplicate', r'recovered_file']
        return any(re.search(p, filename, re.IGNORECASE) for p in patterns)

    def is_likely_duplicate(self, asset, filename):
        patterns = [r'copy', r'duplicate', r'\(\d+\)', r'_copy', r'_duplicate', r' - copy']
        return any(re.search(p, filename, re.IGNORECASE) for p in patterns)

    def run_analysis(self, socketio_instance):
        global analysis_running
        analysis_running = True
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM analysis_results')
                conn.commit()
            socketio_instance.emit('analysis_update', {'status': 'Fetching assets from Immich...'})
            assets = self.get_all_assets()
            if not assets:
                socketio_instance.emit('analysis_complete', {'error': 'No assets found or connection failed'})
                return
            total_assets, processed, candidates_found = len(assets), 0, 0
            socketio_instance.emit('analysis_update', {
                'status': f'Analyzing {total_assets} assets...', 'progress': 0, 'total': total_assets
            })
            batch_size = 100
            for i in range(0, total_assets, batch_size):
                batch = assets[i:i + batch_size]
                with sqlite3.connect(self.db_path) as conn:
                    for asset in batch:
                        analysis_result = self.analyze_asset(asset)
                        if analysis_result['confidence'] > 0.5:
                            conn.execute('''
                                INSERT INTO analysis_results 
                                (id, filename, file_path, category, confidence_score, reasons, 
                                 file_size, created_date, analysis_date)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                asset.get('id', ''),
                                asset.get('originalFileName', ''),
                                asset.get('originalPath', ''),
                                analysis_result['category'],
                                analysis_result['confidence'],
                                analysis_result['reasons'],
                                asset.get('exifInfo', {}).get('fileSizeInByte', 0),
                                asset.get('fileCreatedAt', ''),
                                datetime.now().isoformat()
                            ))
                            candidates_found += 1
                        processed += 1
                        if processed % 50 == 0:
                            progress = (processed / total_assets) * 100
                            socketio_instance.emit('analysis_update', {
                                'status': f'Processed {processed}/{total_assets} assets...',
                                'progress': progress,
                                'candidates_found': candidates_found
                            })
                    conn.commit()
            logger.info(f"Batch analysis completed. Processed {processed} assets")
            socketio_instance.emit('analysis_complete', {
                'total_processed': processed,
                'candidates_found': candidates_found
            })
        except Exception as e:
            logger.error(f"Analysis error: {str(e)}")
            socketio_instance.emit('analysis_complete', {'error': str(e)})
        finally:
            analysis_running = False

@app.route('/')
def index():
    return render_template('cleaner.html')

@app.route('/api/start_analysis', methods=['POST'])
def start_analysis():
    global analysis_running
    if not cleaner_engine:
        return jsonify({'success': False, 'message': 'Not configured'}), 400
    if analysis_running:
        return jsonify({'success': False, 'message': 'Already running'}), 400
    thread = threading.Thread(target=cleaner_engine.run_analysis, args=(socketio,))
    thread.daemon = True
    thread.start()
    return jsonify({'success': True, 'message': 'Started'})

@app.route('/api/config', methods=['POST'])
def save_config():
    global cleaner_engine
    try:
        data = request.get_json()
        immich_url = data.get('immich_url', '').strip()
        api_key = data.get('api_key', '').strip()
        if not immich_url or not api_key:
            return jsonify({'success': False, 'message': 'URL and API key required'}), 400
        cleaner_engine = ImmichImageCleaner(immich_url, api_key)
        success, message = cleaner_engine.test_connection()
        if success:
            config = {'immich_url': immich_url, 'api_key': api_key}
            with open('/app/data/config.json', 'w') as f:
                json.dump(config, f)
            return jsonify({'success': True, 'message': 'Saved and tested OK'})
        else:
            cleaner_engine = None
            return jsonify({'success': False, 'message': message}), 400
    except Exception as e:
        logger.error(f"Config error: {str(e)}")
        cleaner_engine = None
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/config', methods=['GET'])
def get_config():
    try:
        with open('/app/data/config.json', 'r') as f:
            config = json.load(f)
        return jsonify({
            'immich_url': config.get('immich_url', ''),
            'has_api_key': bool(config.get('api_key'))
        })
    except:
        return jsonify({'immich_url': '', 'has_api_key': False})

@app.route('/api/results')
def get_results():
    if not cleaner_engine:
        return jsonify({'error': 'Not configured'}), 400
    try:
        category = request.args.get('category', 'all')
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            cursor = conn.execute('''
                SELECT * FROM analysis_results WHERE category = ?
            ''', (category,)) if category != 'all' else conn.execute('''
                SELECT * FROM analysis_results ORDER BY confidence_score DESC
            ''')
            columns = [description[0] for description in cursor.description]
            return jsonify([dict(zip(columns, row)) for row in cursor.fetchall()])
    except Exception as e:
        logger.error(f"Results error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

def load_startup_config():
    global cleaner_engine
    try:
        immich_url = os.getenv('IMMICH_URL')
        api_key = os.getenv('IMMICH_API_KEY')
        if immich_url and api_key:
            cleaner_engine = ImmichImageCleaner(immich_url, api_key)
            with open('/app/data/config.json', 'w') as f:
                json.dump({'immich_url': immich_url, 'api_key': api_key}, f)
        else:
            with open('/app/data/config.json', 'r') as f:
                config = json.load(f)
                cleaner_engine = ImmichImageCleaner(config['immich_url'], config['api_key'])
    except Exception as e:
        logger.info(f"No config found: {str(e)}")

if __name__ == '__main__':
    load_startup_config()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

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
        """Ensure data directory exists"""
        os.makedirs('/app/data', exist_ok=True)
        os.makedirs('/app/logs', exist_ok=True)
        
    def init_database(self):
        """Initialize SQLite database for storing analysis results"""
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
        """Test connection to Immich API"""
        try:
            # Use the working search/metadata endpoint for connection test
            if not self.immich_url.endswith('/api'):
                test_url = f"{self.immich_url}/api/search/metadata"
            else:
                test_url = f"{self.immich_url}/search/metadata"
            
            # Test with empty POST request
            response = requests.post(test_url, headers=self.headers, json={}, timeout=10)
            logger.info(f"Testing connection to: {test_url}")
            logger.info(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                # Check if we get the expected structure
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
        """Fetch all assets from Immich using search/metadata endpoint"""
        try:
            # Use the correct search/metadata endpoint
            if not self.immich_url.endswith('/api'):
                url = f"{self.immich_url}/api/search/metadata"
            else:
                url = f"{self.immich_url}/search/metadata"
            
            # Use POST with empty body to get all assets
            response = requests.post(url, headers=self.headers, json={}, timeout=30)
            logger.info(f"Fetching assets from: {url}")
            
            if response.status_code == 200:
                data = response.json()
                # Extract assets from the response structure
                if 'assets' in data and 'items' in data['assets']:
                    assets = data['assets']['items']
                    logger.info(f"Successfully fetched {len(assets)} assets")
                    return assets
                else:
                    logger.error(f"Unexpected response structure: {list(data.keys())}")
                    return []
            else:
                logger.error(f"Failed to fetch assets: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching assets: {str(e)}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response: {str(e)}")
            logger.error(f"Response content: {response.text[:500]}...")
            return []
    
    def analyze_asset(self, asset):
        """Analyze a single asset for cleanup candidates"""
        reasons = []
        confidence = 0.0
        category = "unknown"
        
        filename = asset.get('originalFileName', '').lower()
        file_path = asset.get('originalPath', '')
        
        # Screenshot detection
        if self.is_screenshot(asset, filename):
            category = "screenshot"
            reasons.append("Screenshot detected")
            confidence += 0.8
            
        # Web cache detection
        elif self.is_web_cache(asset, filename, file_path):
            category = "web_cache"
            reasons.append("Web cache/thumbnail detected")
            confidence += 0.7
            
        # Data recovery artifacts
        elif self.is_recovery_artifact(asset, filename, file_path):
            category = "recovery_artifact"
            reasons.append("Data recovery artifact")
            confidence += 0.9
            
        # Duplicate detection (basic)
        elif self.is_likely_duplicate(asset, filename):
            category = "duplicate"
            reasons.append("Potential duplicate")
            confidence += 0.6
            
        return {
            'category': category,
            'confidence': min(confidence, 1.0),
            'reasons': '; '.join(reasons)
        }
    
    def is_screenshot(self, asset, filename):
        """Detect screenshots based on various criteria"""
        screenshot_patterns = [
            r'screenshot', r'screen_shot', r'screen shot',
            r'img_\d+', r'image_\d+',
            r'photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}',
            r'screenshot_\d+', r'capture_\d+'
        ]
        
        for pattern in screenshot_patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
                
        # Check for common screenshot resolutions
        if 'exifInfo' in asset:
            exif = asset['exifInfo']
            if exif.get('imageWidth') and exif.get('imageHeight'):
                width, height = exif['imageWidth'], exif['imageHeight']
                # Common mobile screenshot resolutions
                screenshot_resolutions = [
                    (1080, 1920), (1440, 2560), (1125, 2436),
                    (828, 1792), (750, 1334), (1080, 2340)
                ]
                if (width, height) in screenshot_resolutions or (height, width) in screenshot_resolutions:
                    return True
        
        return False
    
    def is_web_cache(self, asset, filename, file_path):
        """Detect web cache and thumbnail files"""
        cache_indicators = [
            'cache', 'thumb', 'thumbnail', 'temp', 'tmp',
            'preview', 'avatar', 'profile', 'social',
            'facebook', 'instagram', 'twitter', 'whatsapp'
        ]
        
        for indicator in cache_indicators:
            if indicator in filename or indicator in file_path.lower():
                return True
                
        return False
    
    def is_recovery_artifact(self, asset, filename, file_path):
        """Detect data recovery artifacts"""
        recovery_patterns = [
            r'recovered', r'restore', r'backup',
            r'file_\d+', r'img_\d{4}', r'dsc_\d+',
            r'copy_of', r'duplicate', r'recovered_file'
        ]
        
        for pattern in recovery_patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
                
        return False
    
    def is_likely_duplicate(self, asset, filename):
        """Basic duplicate detection"""
        duplicate_patterns = [
            r'copy', r'duplicate', r'\(\d+\)',
            r'_copy', r'_duplicate', r' - copy'
        ]
        
        for pattern in duplicate_patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
                
        return False
    
    def run_analysis(self, socketio_instance):
        """Run the full analysis process"""
        global analysis_running
        analysis_running = True
        
        try:
            # Clear previous results
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM analysis_results')
                conn.commit()
            
            # Fetch all assets
            socketio_instance.emit('analysis_update', {'status': 'Fetching assets from Immich...'})
            assets = self.get_all_assets()
            
            if not assets:
                socketio_instance.emit('analysis_complete', {'error': 'No assets found or connection failed'})
                return
            
            total_assets = len(assets)
            processed = 0
            candidates_found = 0
            
            socketio_instance.emit('analysis_update', {
                'status': f'Analyzing {total_assets} assets...',
                'progress': 0,
                'total': total_assets
            })
            
            # Process assets in batches
            batch_size = 100
            for i in range(0, total_assets, batch_size):
                batch = assets[i:i + batch_size]
                
                with sqlite3.connect(self.db_path) as conn:
                    for asset in batch:
                        analysis_result = self.analyze_asset(asset)
                        
                        if analysis_result['confidence'] > 0.5:  # Only store candidates
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
                        
                        # Update progress
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

# Routes
@app.route('/')
def index():
    return render_template('cleaner.html')

@app.route('/api/version')
def get_version():
    """Get application version"""
    try:
        with open('/app/VERSION', 'r') as f:
            version = f.read().strip()
        return jsonify({'version': version})
    except:
        return jsonify({'version': 'unknown'})

@app.route('/api/config', methods=['POST'])
def save_config():
    """Save configuration and test connection"""
    global cleaner_engine
    
    try:
        data = request.get_json()
        immich_url = data.get('immich_url', '').strip()
        api_key = data.get('api_key', '').strip()
        
        if not immich_url or not api_key:
            return jsonify({'success': False, 'message': 'URL and API key are required'}), 400
        
        # Create cleaner engine
        cleaner_engine = ImmichImageCleaner(immich_url, api_key)
        
        # Test connection
        success, message = cleaner_engine.test_connection()
        
        if success:
            # Save config to file
            config = {'immich_url': immich_url, 'api_key': api_key}
            with open('/app/data/config.json', 'w') as f:
                json.dump(config, f)
            
            return jsonify({
                'success': True, 
                'message': 'Configuration saved and connection tested successfully!'
            })
        else:
            cleaner_engine = None
            return jsonify({'success': False, 'message': message}), 400
            
    except Exception as e:
        logger.error(f"Configuration error: {str(e)}")
        cleaner_engine = None
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    try:
        with open('/app/data/config.json', 'r') as f:
            config = json.load(f)
        # Don't return the API key for security
        return jsonify({
            'immich_url': config.get('immich_url', ''),
            'has_api_key': bool(config.get('api_key'))
        })
    except:
        return jsonify({'immich_url': '', 'has_api_key': False})

@app.route('/api/start_analysis', methods=['POST'])
def start_analysis():
    """Start the analysis process"""
    global analysis_running
    
    if not cleaner_engine:
        return jsonify({'success': False, 'message': 'Not configured. Please save configuration first.'}), 400
    
    if analysis_running:
        return jsonify({'success': False, 'message': 'Analysis already running'}), 400
    
    # Start analysis in background thread
    thread = threading.Thread(target=cleaner_engine.run_analysis, args=(socketio,))
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'Analysis started'})

@app.route('/api/analyze/start', methods=['POST'])
def start_analysis_alt():
    """Alternative endpoint for starting analysis (used by web interface)"""
    return start_analysis()

@app.route('/api/results')
def get_results():
    """Get analysis results"""
    if not cleaner_engine:
        return jsonify({'error': 'Not configured'}), 400
        
    try:
        category = request.args.get('category', 'all')
        
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            if category == 'all':
                cursor = conn.execute('''
                    SELECT * FROM analysis_results 
                    ORDER BY confidence_score DESC, category
                ''')
            else:
                cursor = conn.execute('''
                    SELECT * FROM analysis_results 
                    WHERE category = ? 
                    ORDER BY confidence_score DESC
                ''', (category,))
            
            columns = [description[0] for description in cursor.description]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
            
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error fetching results: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/statistics')
def get_statistics():
    """Get analysis statistics"""
    if not cleaner_engine:
        return jsonify({'error': 'Not configured'}), 400
        
    try:
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            # Get category counts
            cursor = conn.execute('''
                SELECT category, COUNT(*) as count, 
                       AVG(confidence_score) as avg_confidence,
                       SUM(file_size) as total_size
                FROM analysis_results 
                GROUP BY category
            ''')
            
            stats = {}
            for row in cursor:
                stats[row[0]] = {
                    'count': row[1],
                    'avg_confidence': row[2],
                    'total_size': row[3] or 0
                }
            
            # Get total count
            cursor = conn.execute('SELECT COUNT(*) FROM analysis_results')
            total_count = cursor.fetchone()[0]
            
            return jsonify({
                'total_candidates': total_count,
                'by_category': stats
            })
    except Exception as e:
        logger.error(f"Error fetching statistics: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/mark_for_deletion', methods=['POST'])
def mark_for_deletion():
    """Mark items for deletion"""
    if not cleaner_engine:
        return jsonify({'error': 'Not configured'}), 400
        
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])
        
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            placeholders = ','.join(['?' for _ in asset_ids])
            conn.execute(f'''
                UPDATE analysis_results 
                SET marked_for_deletion = 1 
                WHERE id IN ({placeholders})
            ''', asset_ids)
            conn.commit()
        
        return jsonify({'success': True, 'marked_count': len(asset_ids)})
    except Exception as e:
        logger.error(f"Error marking for deletion: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Socket.IO events
@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

# Load config on startup
def load_startup_config():
    """Load configuration on application startup"""
    global cleaner_engine
    try:
        # Check if environment variables are set
        immich_url = os.getenv('IMMICH_URL')
        api_key = os.getenv('IMMICH_API_KEY')
        
        if immich_url and api_key:
            logger.info("Loading configuration from environment variables")
            cleaner_engine = ImmichImageCleaner(immich_url, api_key)
            
            # Save to config file for web interface
            config = {'immich_url': immich_url, 'api_key': api_key}
            with open('/app/data/config.json', 'w') as f:
                json.dump(config, f)
        else:
            # Try loading from config file
            with open('/app/data/config.json', 'r') as f:
                config = json.load(f)
                cleaner_engine = ImmichImageCleaner(
                    config['immich_url'], 
                    config['api_key']
                )
                logger.info("Configuration loaded from file")
    except Exception as e:
        logger.info(f"No saved configuration found: {str(e)}")

if __name__ == '__main__':
    load_startup_config()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

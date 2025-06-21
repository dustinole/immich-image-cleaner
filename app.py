from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS
import os
import json
import io
import threading
import logging
from immich_cleaner import ImmichCleaner
import requests
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
cleaner_engine = None
analysis_thread = None
analysis_status = {
    'running': False,
    'progress': 0,
    'total': 0,
    'current_file': '',
    'start_time': None,
    'found_count': 0
}

@app.route('/')
def index():
    return render_template('cleaner.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    return jsonify({
        'immich_url': os.getenv('IMMICH_URL', 'http://localhost:2283'),
        'immich_api_key': os.getenv('IMMICH_API_KEY', ''),
        'timezone': os.getenv('TZ', 'UTC')
    })

@app.route('/api/config', methods=['POST'])
def save_config():
    """Save configuration and test connection"""
    data = request.json
    immich_url = data.get('immich_url', '').rstrip('/')
    api_key = data.get('immich_api_key', '')
    
    # Test connection
    try:
        headers = {
            'X-Api-Key': api_key,
            'Content-Type': 'application/json'
        }
        # Use the correct endpoint for the user's Immich version
        response = requests.post(
            f"{immich_url}/api/search/metadata",
            headers=headers,
            json={},
            timeout=10
        )
        
        if response.status_code == 200:
            # Save to environment (in a real app, you'd persist this)
            os.environ['IMMICH_URL'] = immich_url
            os.environ['IMMICH_API_KEY'] = api_key
            
            # Initialize cleaner engine with new config
            global cleaner_engine
            cleaner_engine = ImmichCleaner(immich_url, api_key)
            
            return jsonify({
                'success': True,
                'message': 'Configuration saved and connection tested successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Connection failed: {response.status_code}'
            }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Connection error: {str(e)}'
        }), 400

@app.route('/api/analyze/start', methods=['POST'])
def start_analysis():
    """Start the analysis process - FIXED ENDPOINT"""
    global analysis_thread, analysis_status, cleaner_engine
    
    if analysis_status['running']:
        return jsonify({
            'success': False,
            'message': 'Analysis already in progress'
        }), 400
    
    if not cleaner_engine:
        return jsonify({
            'success': False,
            'message': 'Please configure Immich connection first'
        }), 400
    
    # Reset status
    analysis_status = {
        'running': True,
        'progress': 0,
        'total': 0,
        'current_file': '',
        'start_time': datetime.now(),
        'found_count': 0
    }
    
    # Start analysis in background thread
    analysis_thread = threading.Thread(target=run_analysis)
    analysis_thread.start()
    
    return jsonify({
        'success': True,
        'message': 'Analysis started'
    })

@app.route('/api/analyze/stop', methods=['POST'])
def stop_analysis():
    """Stop the analysis process"""
    global analysis_status
    
    if not analysis_status['running']:
        return jsonify({
            'success': False,
            'message': 'No analysis in progress'
        }), 400
    
    analysis_status['running'] = False
    
    return jsonify({
        'success': True,
        'message': 'Analysis stop requested'
    })

@app.route('/api/analyze/status', methods=['GET'])
def get_analysis_status():
    """Get current analysis status"""
    status = analysis_status.copy()
    
    if status['start_time']:
        elapsed = (datetime.now() - status['start_time']).total_seconds()
        status['elapsed_time'] = int(elapsed)
        
        # Estimate remaining time
        if status['progress'] > 0:
            rate = status['progress'] / elapsed
            remaining = (status['total'] - status['progress']) / rate if rate > 0 else 0
            status['estimated_remaining'] = int(remaining)
    
    return jsonify(status)

@app.route('/api/results', methods=['GET'])
def get_results():
    """Get analysis results"""
    if not cleaner_engine or not hasattr(cleaner_engine, 'db_path'):
        return jsonify({
            'screenshots': [],
            'web_files': [],
            'recovery_artifacts': []
        })
    
    try:
        results = cleaner_engine.get_results()
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error getting results: {e}")
        return jsonify({
            'screenshots': [],
            'web_files': [],
            'recovery_artifacts': []
        })

@app.route('/api/statistics', methods=['GET'])
def get_statistics():
    """Get analysis statistics"""
    if not cleaner_engine or not hasattr(cleaner_engine, 'db_path'):
        return jsonify({
            'total_analyzed': 0,
            'screenshots_found': 0,
            'web_files_found': 0,
            'recovery_artifacts_found': 0,
            'total_size_mb': 0,
            'marked_for_deletion': 0
        })
    
    try:
        stats = cleaner_engine.get_statistics()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return jsonify({
            'total_analyzed': 0,
            'screenshots_found': 0,
            'web_files_found': 0,
            'recovery_artifacts_found': 0,
            'total_size_mb': 0,
            'marked_for_deletion': 0
        })

@app.route('/api/mark_for_deletion', methods=['POST'])
def mark_for_deletion():
    """Mark assets for deletion"""
    if not cleaner_engine:
        return jsonify({
            'success': False,
            'message': 'Cleaner engine not initialized'
        }), 400
    
    data = request.json
    asset_ids = data.get('asset_ids', [])
    mark = data.get('mark', True)
    
    try:
        cleaner_engine.mark_for_deletion(asset_ids, mark)
        return jsonify({
            'success': True,
            'message': f'{len(asset_ids)} assets marked for deletion'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    """Export results as CSV"""
    if not cleaner_engine:
        return jsonify({
            'success': False,
            'message': 'No results available'
        }), 400
    
    try:
        csv_path = cleaner_engine.export_to_csv()
        return send_file(csv_path, as_attachment=True, download_name='immich_cleanup_results.csv')
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/proxy/thumbnail/<asset_id>')
def proxy_thumbnail(asset_id):
    """Proxy endpoint to fetch Immich thumbnails"""
    try:
        immich_url = os.getenv('IMMICH_URL', '')
        api_key = os.getenv('IMMICH_API_KEY', '')
        
        if not immich_url or not api_key:
            return jsonify({'error': 'Not configured'}), 500
        
        # Try different Immich endpoints
        endpoints = [
            f"{immich_url}/api/asset/thumbnail/{asset_id}?size=preview",
            f"{immich_url}/api/assets/{asset_id}/thumbnail?size=preview",
            f"{immich_url}/api/asset/thumbnail/{asset_id}",
            f"{immich_url}/api/asset/file/{asset_id}?isThumb=true"
        ]
        
        headers = {
            'X-Api-Key': api_key,
            'Accept': 'image/*'
        }
        
        for endpoint in endpoints:
            try:
                response = requests.get(endpoint, headers=headers, timeout=10)
                if response.status_code == 200:
                    # Return the image with proper headers
                    return send_file(
                        io.BytesIO(response.content),
                        mimetype=response.headers.get('Content-Type', 'image/jpeg'),
                        as_attachment=False
                    )
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {e}")
                continue
        
        # If all endpoints fail, return error
        return jsonify({'error': 'Failed to fetch thumbnail'}), 404
        
    except Exception as e:
        logger.error(f"Error proxying thumbnail: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete', methods=['POST'])
def delete_assets():
    """Delete assets directly via Immich API"""
    if not cleaner_engine:
        return jsonify({
            'success': False,
            'message': 'Cleaner engine not initialized'
        }), 400
    
    data = request.json
    asset_ids = data.get('asset_ids', [])
    
    if not asset_ids:
        return jsonify({
            'success': False,
            'message': 'No assets to delete'
        }), 400
    
    try:
        # Delete via Immich API - try different endpoints
        headers = {
            'X-Api-Key': cleaner_engine.api_key,
            'Content-Type': 'application/json'
        }
        
        # Try the newer bulk delete endpoint first
        delete_data = {
            "ids": asset_ids,
            "force": True
        }
        
        # Try different Immich API endpoints
        endpoints = [
            f"{cleaner_engine.base_url}/api/assets",
            f"{cleaner_engine.base_url}/api/asset"
        ]
        
        delete_success = False
        for endpoint in endpoints:
            try:
                delete_response = requests.delete(
                    endpoint,
                    headers=headers,
                    json=delete_data,
                    timeout=30
                )
                
                if delete_response.status_code in [200, 204]:
                    delete_success = True
                    break
                else:
                    logger.warning(f"Delete endpoint {endpoint} returned {delete_response.status_code}")
            except Exception as e:
                logger.warning(f"Delete endpoint {endpoint} failed: {e}")
                continue
        
        if delete_success:
            # Remove from our database
            cleaner_engine.remove_deleted_assets(asset_ids)
            
            return jsonify({
                'success': True,
                'message': f'Successfully deleted {len(asset_ids)} assets',
                'deleted_count': len(asset_ids)
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to delete assets - check Immich API'
            }), 500
            
    except Exception as e:
        logger.error(f"Error deleting assets: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/feedback', methods=['POST'])
def save_feedback():
    """Save user feedback for learning"""
    data = request.json
    
    # Log feedback to a file for future improvements
    feedback_file = '/data/feedback_log.json'
    
    try:
        # Load existing feedback
        if os.path.exists(feedback_file):
            with open(feedback_file, 'r') as f:
                feedback_data = json.load(f)
        else:
            feedback_data = []
        
        # Add new feedback
        feedback_data.append(data)
        
        # Save updated feedback
        os.makedirs(os.path.dirname(feedback_file), exist_ok=True)
        with open(feedback_file, 'w') as f:
            json.dump(feedback_data, f, indent=2)
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/export/deletion_script', methods=['GET'])
def export_deletion_script():
    """Export deletion script"""
    if not cleaner_engine:
        return jsonify({
            'success': False,
            'message': 'No results available'
        }), 400
    
    try:
        script_path = cleaner_engine.generate_deletion_script()
        return send_file(script_path, as_attachment=True, download_name='delete_assets.sh')
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

def run_analysis():
    """Run the analysis in background"""
    global analysis_status, cleaner_engine
    
    try:
        logger.info("Starting analysis...")
        
        # Get total count first
        headers = {
            'X-Api-Key': cleaner_engine.api_key,
            'Content-Type': 'application/json'
        }
        
        # Get first page to determine total
        response = requests.post(
            f"{cleaner_engine.base_url}/api/search/metadata",
            headers=headers,
            json={},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            # Handle the response structure: data.assets.items
            if 'assets' in data and 'items' in data['assets']:
                first_batch = data['assets']['items']
                # Estimate total (this is approximate, we'll update as we go)
                analysis_status['total'] = len(first_batch) * 1000  # Rough estimate
                
                # Process first batch
                for idx, asset in enumerate(first_batch):
                    if not analysis_status['running']:
                        break
                    
                    analysis_status['current_file'] = asset.get('originalFileName', 'Unknown')
                    analysis_status['progress'] = idx + 1
                    
                    # Analyze asset
                    if cleaner_engine.analyze_asset(asset):
                        analysis_status['found_count'] += 1
                
                # Continue with pagination if available
                next_page = data['assets'].get('nextPage')
                page_count = 1
                
                while next_page and analysis_status['running']:
                    response = requests.post(
                        f"{cleaner_engine.base_url}/api/search/metadata",
                        headers=headers,
                        json={'page': next_page},
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if 'assets' in data and 'items' in data['assets']:
                            batch = data['assets']['items']
                            
                            for idx, asset in enumerate(batch):
                                if not analysis_status['running']:
                                    break
                                
                                analysis_status['current_file'] = asset.get('originalFileName', 'Unknown')
                                analysis_status['progress'] += 1
                                
                                # Analyze asset
                                if cleaner_engine.analyze_asset(asset):
                                    analysis_status['found_count'] += 1
                            
                            next_page = data['assets'].get('nextPage')
                            page_count += 1
                            
                            # Update total estimate
                            analysis_status['total'] = analysis_status['progress'] + (len(batch) * 10)
                        else:
                            break
                    else:
                        logger.error(f"Error fetching page {page_count}: {response.status_code}")
                        break
        
        logger.info(f"Analysis completed. Analyzed {analysis_status['progress']} assets, found {analysis_status['found_count']} cleanup candidates")
        
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        analysis_status['error'] = str(e)
    finally:
        analysis_status['running'] = False

if __name__ == '__main__':
    # Initialize cleaner engine if config exists
    immich_url = os.getenv('IMMICH_URL')
    api_key = os.getenv('IMMICH_API_KEY')
    
    if immich_url and api_key:
        cleaner_engine = ImmichCleaner(immich_url, api_key)
        logger.info(f"Initialized with Immich URL: {immich_url}")
    
    app.run(host='0.0.0.0', port=5001, debug=False)

#!/usr/bin/env python3
"""
Immich Image Cleaner Plugin
A standalone service for detecting and managing unwanted images in your Immich library
Focuses on screenshots, web cache, thumbnails, and data recovery artifacts
"""

import os
import logging
import json
import hashlib
import mimetypes
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import threading
import time
import sqlite3

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import requests
import cv2
import numpy as np
from PIL import Image, ExifTags
import magic
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/image_cleaner.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
socketio = SocketIO(app, cors_allowed_origins="*")

class ImmichAPIClient:
    """Client for interacting with Immich API"""
    
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,
            'Content-Type': 'application/json'
        })
    
    def get_assets(self, page: int = 1, size: int = 1000) -> List[Dict]:
        """Get assets from Immich"""
        try:
            response = self.session.get(
                f"{self.base_url}/assets",
                params={'page': page, 'size': size}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching assets: {e}")
            return []
    
    def get_asset_info(self, asset_id: str) -> Optional[Dict]:
        """Get detailed asset information"""
        try:
            response = self.session.get(f"{self.base_url}/assets/{asset_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching asset {asset_id}: {e}")
            return None
    
    def get_faces_for_asset(self, asset_id: str) -> List[Dict]:
        """Get faces detected in an asset using Immich's ML"""
        try:
            response = self.session.get(f"{self.base_url}/faces", 
                                      params={'assetId': asset_id})
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logger.error(f"Error fetching faces for asset {asset_id}: {e}")
            return []
    
    def get_smart_search_data(self, asset_id: str) -> Optional[Dict]:
        """Get smart search/ML analysis data for an asset"""
        try:
            response = self.session.get(f"{self.base_url}/search/metadata/{asset_id}")
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error fetching ML data for asset {asset_id}: {e}")
            return None
    
    def get_asset_statistics(self) -> Optional[Dict]:
        """Get overall asset statistics from Immich"""
        try:
            response = self.session.get(f"{self.base_url}/server-info/statistics")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching statistics: {e}")
            return None
    
    def download_asset(self, asset_id: str, thumbnail: bool = True) -> Optional[bytes]:
        """Download asset image data (only when needed for custom analysis)"""
        try:
            endpoint = f"/assets/{asset_id}/thumbnail" if thumbnail else f"/assets/{asset_id}/original"
            response = self.session.get(f"{self.base_url}{endpoint}")
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Error downloading asset {asset_id}: {e}")
            return None
    
    def delete_asset(self, asset_id: str) -> bool:
        """Delete an asset from Immich"""
        try:
            response = self.session.delete(f"{self.base_url}/assets/{asset_id}")
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error deleting asset {asset_id}: {e}")
            return False

class UnwantedImageDetector:
    """Comprehensive detector for unwanted images"""
    
    def __init__(self):
        self.screenshot_patterns = [
            # Common screenshot patterns
            r'screenshot', r'screen_shot', r'screen shot', r'capture', r'snap',
            r'img_\d{4}', r'image_\d{4}', r'vlcsnap', r'snapshot',
            # Mobile patterns
            r'screenshot_\d{8}', r'screen_\d{8}', r'capture_\d{8}',
            # Windows patterns
            r'clipboard', r'prtscr', r'snip',
            # Web patterns
            r'webpage', r'browser', r'tab_', r'window_'
        ]
        
        self.web_cache_patterns = [
            # Browser cache patterns
            r'cache', r'temp', r'tmp', r'preview', r'thumb',
            r'avatar', r'profile_pic', r'icon', r'favicon',
            r'banner', r'header', r'footer', r'widget',
            # Social media patterns
            r'fb_', r'twitter_', r'insta_', r'snap_', r'tiktok_',
            # Web artifacts
            r'advertisement', r'ads_', r'promo', r'popup'
        ]
        
        self.low_quality_indicators = [
            # Size indicators (pixels)
            (100, 100),  # Very small images
            (150, 150),  # Likely thumbnails
            (200, 200),  # Small previews
        ]
        
        # Common screen resolutions (suggests screenshots)
        self.screenshot_resolutions = [
            (1920, 1080), (1366, 768), (1280, 720), (1024, 768),
            (1440, 900), (1600, 900), (1680, 1050), (1920, 1200),
            # Mobile resolutions
            (414, 896), (375, 667), (360, 640), (320, 568),
            (411, 731), (393, 851), (428, 926),
            # Tablet resolutions
            (768, 1024), (1024, 1366), (820, 1180)
        ]
    
    def analyze_image(self, asset_id: str, asset_info: Dict, immich_client: 'ImmichAPIClient') -> Dict:
        """Comprehensive analysis using Immich's existing ML data and minimal additional processing"""
        try:
            analysis_result = {
                'asset_id': asset_id,
                'filename': asset_info.get('originalFileName', ''),
                'file_path': asset_info.get('originalPath', ''),
                'file_size': asset_info.get('originalSize', 0),
                'mime_type': asset_info.get('type', 'unknown'),
                'is_screenshot': False,
                'is_web_cache': False,
                'is_low_quality': False,
                'is_duplicate': False,
                'is_corrupt': False,
                'confidence_score': 0.0,
                'flags': [],
                'recommendations': [],
                'analysis_date': datetime.now().isoformat(),
                'has_faces': False,
                'face_count': 0
            }
            
            # Get existing ML analysis from Immich
            faces_data = immich_client.get_faces_for_asset(asset_id)
            smart_data = immich_client.get_smart_search_data(asset_id)
            
            # Use Immich's existing face detection results
            if faces_data:
                analysis_result['has_faces'] = True
                analysis_result['face_count'] = len(faces_data)
                analysis_result['flags'].append(f"Immich detected {len(faces_data)} faces")
            
            # Analyze different aspects (prioritizing metadata over image processing)
            self._analyze_filename(analysis_result)
            self._analyze_file_path(analysis_result)
            self._analyze_asset_metadata(analysis_result, asset_info)
            self._analyze_exif_data(analysis_result, asset_info)
            
            # Only do heavy image processing if needed for high-confidence cases
            needs_image_analysis = (
                analysis_result['is_screenshot'] or 
                analysis_result['is_web_cache'] or
                not analysis_result['has_faces']  # No faces detected by Immich
            )
            
            if needs_image_analysis and analysis_result['file_size'] < 10_000_000:  # < 10MB
                self._analyze_image_content_lightweight(analysis_result, asset_info, immich_client)
            
            # Calculate overall confidence
            self._calculate_confidence(analysis_result)
            
            # Generate recommendations
            self._generate_recommendations(analysis_result)
            
            return analysis_result
            
        except Exception as e:
            logger.error(f"Error analyzing image {asset_id}: {e}")
            return {'error': str(e), 'asset_id': asset_id}
    
    def _analyze_filename(self, result: Dict):
        """Analyze filename for unwanted patterns"""
        filename = result['filename'].lower()
        
        # Screenshot detection
        for pattern in self.screenshot_patterns:
            if pattern.replace('\\', '') in filename:
                result['is_screenshot'] = True
                result['flags'].append(f"Screenshot pattern: {pattern}")
                break
        
        # Web cache detection
        for pattern in self.web_cache_patterns:
            if pattern.replace('\\', '') in filename:
                result['is_web_cache'] = True
                result['flags'].append(f"Web cache pattern: {pattern}")
                break
        
        # Generic unwanted patterns
        unwanted_keywords = [
            'temp', 'tmp', 'cache', 'backup', 'copy', 'duplicate',
            'untitled', 'unknown', 'recovered', 'restored'
        ]
        
        for keyword in unwanted_keywords:
            if keyword in filename:
                result['flags'].append(f"Unwanted keyword: {keyword}")
    
    def _analyze_file_path(self, result: Dict):
        """Analyze file path for indicators"""
        file_path = result['file_path'].lower()
        
        # Common unwanted directories
        unwanted_dirs = [
            'temp', 'tmp', 'cache', 'thumbnails', 'thumbs',
            'preview', 'downloads', 'screenshots', 'captures',
            'browser', 'chrome', 'firefox', 'safari', 'edge',
            'recycle', 'trash', 'deleted'
        ]
        
        for dir_name in unwanted_dirs:
            if dir_name in file_path:
                result['flags'].append(f"Unwanted directory: {dir_name}")
    
    def _analyze_asset_metadata(self, result: Dict, asset_info: Dict):
        """Analyze asset metadata from Immich"""
        # Check dimensions from Immich metadata
        exif_info = asset_info.get('exifInfo', {})
        
        # Get image dimensions from EXIF
        width = exif_info.get('imageWidth') or exif_info.get('exifImageWidth')
        height = exif_info.get('imageHeight') or exif_info.get('exifImageHeight')
        
        if width and height:
            result.update({
                'width': width,
                'height': height,
                'aspect_ratio': width / height
            })
            
            # Check for very small images (likely thumbnails)
            for max_width, max_height in self.low_quality_indicators:
                if width <= max_width and height <= max_height:
                    result['is_low_quality'] = True
                    result['flags'].append(f"Small size: {width}x{height}")
                    break
            
            # Check for exact screenshot resolutions
            if (width, height) in self.screenshot_resolutions:
                result['is_screenshot'] = True
                result['flags'].append(f"Screenshot resolution: {width}x{height}")
            
            # Check for unusual aspect ratios (might indicate crops/artifacts)
            aspect_ratio = width / height
            if aspect_ratio > 5 or aspect_ratio < 0.2:  # Very wide or very tall
                result['flags'].append(f"Unusual aspect ratio: {aspect_ratio:.2f}")
        
        # Check file size
        file_size = result['file_size']
        if file_size < 10000:  # Less than 10KB
            result['is_low_quality'] = True
            result['flags'].append("Very small file size")
        elif file_size > 50000000:  # Greater than 50MB
            result['flags'].append("Unusually large file")
    
    def _analyze_exif_data(self, result: Dict, asset_info: Dict):
        """Analyze EXIF data for indicators"""
        exif_info = asset_info.get('exifInfo', {})
        
        # No camera information often indicates non-camera source
        if not exif_info.get('make') and not exif_info.get('model'):
            result['flags'].append("No camera information")
        
        # Check software field for screenshot indicators
        software = exif_info.get('software', '').lower()
        screenshot_software = [
            'android', 'ios', 'windows', 'macos', 'linux',
            'screenshot', 'snipping', 'capture', 'paint',
            'photoshop', 'gimp', 'canva', 'figma'
        ]
        
        for soft in screenshot_software:
            if soft in software:
                result['is_screenshot'] = True
                result['flags'].append(f"Screenshot software: {software}")
                break
        
        # Check for missing creation date (common in recovered files)
        if not asset_info.get('fileCreatedAt') and not exif_info.get('dateTimeOriginal'):
            result['flags'].append("Missing creation date")
    
    def _analyze_image_content_lightweight(self, result: Dict, asset_info: Dict, immich_client: 'ImmichAPIClient'):
        """Lightweight image content analysis only when needed"""
        try:
            # Only download thumbnail for content analysis to save bandwidth
            image_data = immich_client.download_asset(result['asset_id'], thumbnail=True)
            if not image_data:
                result['flags'].append("Could not download for analysis")
                return
            
            # Save temporary file for minimal analysis
            temp_path = f'/app/temp/{result["asset_id"]}_thumb.tmp'
            os.makedirs('/app/temp', exist_ok=True)
            with open(temp_path, 'wb') as f:
                f.write(image_data)
            
            try:
                # Basic OpenCV analysis on thumbnail
                img = cv2.imread(temp_path)
                if img is None:
                    result['is_corrupt'] = True
                    result['flags'].append("Cannot read image file")
                    return
                
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
                # Quick UI element detection (simplified for thumbnail)
                ui_elements = self._detect_ui_elements_simple(gray)
                if ui_elements > 10:  # Adjusted threshold for thumbnail
                    result['is_screenshot'] = True
                    result['flags'].append(f"UI elements detected in thumbnail: {ui_elements}")
                
                # Quick uniformity check
                if self._is_mostly_uniform(gray):
                    result['flags'].append("Mostly uniform color (possible generated image)")
                    
            finally:
                # Cleanup
                try:
                    os.remove(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.warning(f"Error in lightweight content analysis: {e}")
            result['flags'].append("Content analysis failed")
    
    def _detect_ui_elements_simple(self, gray_image: np.ndarray) -> int:
        """Simplified UI element detection for thumbnails"""
        try:
            # Simple edge detection
            edges = cv2.Canny(gray_image, 50, 150)
            
            # Count strong horizontal and vertical lines (typical of UI)
            horizontal_lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=20, minLineLength=10, maxLineGap=5)
            vertical_lines = cv2.HoughLinesP(edges, 1, np.pi/2, threshold=20, minLineLength=10, maxLineGap=5)
            
            total_lines = 0
            if horizontal_lines is not None:
                total_lines += len(horizontal_lines)
            if vertical_lines is not None:
                total_lines += len(vertical_lines)
                
            return total_lines
            
        except Exception as e:
            logger.warning(f"Error in simple UI detection: {e}")
            return 0
    
    def _is_mostly_uniform(self, gray_image: np.ndarray) -> bool:
        """Check if image is mostly uniform color"""
        try:
            # Calculate histogram
            hist = cv2.calcHist([gray_image], [0], None, [256], [0, 256])
            
            # Find the most common color
            max_count = np.max(hist)
            total_pixels = gray_image.shape[0] * gray_image.shape[1]
            
            # If more than 80% is the same color
            return (max_count / total_pixels) > 0.8
            
        except Exception as e:
            logger.warning(f"Error checking uniformity: {e}")
            return False
    
    def _calculate_confidence(self, result: Dict):
        """Calculate overall confidence that image is unwanted"""
        confidence = 0.0
        
        # Strong indicators
        if result['is_screenshot']:
            confidence += 0.4
        if result['is_web_cache']:
            confidence += 0.3
        if result['is_low_quality']:
            confidence += 0.2
        if result['is_corrupt']:
            confidence += 0.5
        
        # Flag-based scoring
        flag_score = min(len(result['flags']) * 0.05, 0.3)
        confidence += flag_score
        
        # File size considerations
        if result['file_size'] < 10000:  # Less than 10KB
            confidence += 0.1
        elif result['file_size'] > 50000000:  # Greater than 50MB (unusually large)
            confidence += 0.05
        
        result['confidence_score'] = min(confidence, 1.0)
    
    def _generate_recommendations(self, result: Dict):
        """Generate recommendations based on analysis"""
        recommendations = []
        
        if result['confidence_score'] > 0.8:
            recommendations.append("🗑️ Strongly recommend deletion")
        elif result['confidence_score'] > 0.6:
            recommendations.append("⚠️ Consider for removal")
        elif result['confidence_score'] > 0.4:
            recommendations.append("🤔 Manual review recommended")
        else:
            recommendations.append("✅ Likely keep")
        
        if result['is_screenshot']:
            recommendations.append("📱 Move to screenshots album or delete")
        
        if result['is_web_cache']:
            recommendations.append("🌐 Web artifact - safe to delete")
        
        if result['is_low_quality']:
            recommendations.append("🔍 Check if higher quality version exists")
        
        if result['is_corrupt']:
            recommendations.append("💥 Corrupted file - delete")
        
        result['recommendations'] = recommendations

class ImageCleanerEngine:
    """Main engine for image cleaning operations"""
    
    def __init__(self, immich_client: ImmichAPIClient):
        self.immich_client = immich_client
        self.detector = UnwantedImageDetector()
        self.db_path = '/app/data/cleaner.db'
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for tracking analysis"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS asset_analysis (
                    asset_id TEXT PRIMARY KEY,
                    filename TEXT,
                    file_path TEXT,
                    file_size INTEGER,
                    mime_type TEXT,
                    width INTEGER,
                    height INTEGER,
                    is_screenshot BOOLEAN,
                    is_web_cache BOOLEAN,
                    is_low_quality BOOLEAN,
                    is_duplicate BOOLEAN,
                    is_corrupt BOOLEAN,
                    has_faces BOOLEAN,
                    face_count INTEGER,
                    confidence_score REAL,
                    flags TEXT,
                    recommendations TEXT,
                    analysis_date TEXT,
                    status TEXT DEFAULT 'analyzed',
                    marked_for_deletion BOOLEAN DEFAULT 0
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS duplicate_groups (
                    group_id TEXT,
                    asset_id TEXT,
                    file_hash TEXT,
                    file_size INTEGER,
                    PRIMARY KEY (group_id, asset_id)
                )
            ''')
    
    def analyze_asset(self, asset_id: str) -> Dict:
        """Analyze a single asset leveraging Immich's existing ML data"""
        try:
            # Get asset info
            asset_info = self.immich_client.get_asset_info(asset_id)
            if not asset_info:
                return {'error': 'Could not fetch asset info'}
            
            # Analyze using Immich's existing data + minimal additional processing
            result = self.detector.analyze_image(asset_id, asset_info, self.immich_client)
            
            # Store results
            if 'error' not in result:
                self._save_analysis_result(result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error analyzing asset {asset_id}: {e}")
            return {'error': str(e)}
    
    def _save_analysis_result(self, result: Dict):
        """Save analysis result to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO asset_analysis 
                    (asset_id, filename, file_path, file_size, mime_type, width, height,
                     is_screenshot, is_web_cache, is_low_quality, is_duplicate, is_corrupt,
                     has_faces, face_count, confidence_score, flags, recommendations, analysis_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    result['asset_id'],
                    result['filename'],
                    result['file_path'],
                    result['file_size'],
                    result['mime_type'],
                    result.get('width'),
                    result.get('height'),
                    result['is_screenshot'],
                    result['is_web_cache'],
                    result['is_low_quality'],
                    result['is_duplicate'],
                    result['is_corrupt'],
                    result['has_faces'],
                    result['face_count'],
                    result['confidence_score'],
                    json.dumps(result['flags']),
                    json.dumps(result['recommendations']),
                    result['analysis_date'],
                    'analyzed'
                ))
        except Exception as e:
            logger.error(f"Error saving analysis result: {e}")

# Global variables
immich_client = None
cleaner_engine = None

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('cleaner.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """Configure Immich connection"""
    global immich_client, cleaner_engine
    
    if request.method == 'POST':
        data = request.json
        immich_url = data.get('immich_url')
        api_key = data.get('api_key')
        
        try:
            # Test connection
            test_client = ImmichAPIClient(immich_url, api_key)
            assets = test_client.get_assets(page=1, size=1)
            
            # Save config
            os.makedirs('/app/data', exist_ok=True)
            with open('/app/data/config.json', 'w') as f:
                json.dump({'immich_url': immich_url, 'api_key': api_key}, f)
            
            # Initialize global clients
            immich_client = test_client
            cleaner_engine = ImageCleanerEngine(immich_client)
            
            return jsonify({'success': True, 'message': 'Configuration saved and tested successfully'})
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    
    else:
        # Load existing config
        try:
            with open('/app/data/config.json', 'r') as f:
                config = json.load(f)
                return jsonify(config)
        except:
            return jsonify({'immich_url': '', 'api_key': ''})

@app.route('/api/analyze/start', methods=['POST'])
def start_analysis():
    """Start batch analysis of assets"""
    if not cleaner_engine:
        return jsonify({'error': 'Not configured'}), 400
    
    # Start analysis in background
    threading.Thread(target=run_batch_analysis, daemon=True).start()
    
    return jsonify({'success': True, 'message': 'Analysis started'})

def run_batch_analysis():
    """Run batch analysis of all assets"""
    try:
        logger.info("Starting batch analysis")
        socketio.emit('analysis_status', {'status': 'starting'})
        
        # Get all assets
        page = 1
        total_processed = 0
        
        while True:
            assets = immich_client.get_assets(page=page, size=50)  # Smaller batches for responsiveness
            if not assets:
                break
            
            for asset in assets:
                try:
                    asset_id = asset.get('id')
                    if not asset_id:
                        continue
                    
                    # Skip if already analyzed
                    with sqlite3.connect(cleaner_engine.db_path) as conn:
                        cursor = conn.execute(
                            'SELECT status FROM asset_analysis WHERE asset_id = ?', 
                            (asset_id,)
                        )
                        existing = cursor.fetchone()
                        if existing and existing[0] == 'analyzed':
                            continue
                    
                    # Analyze asset
                    result = cleaner_engine.analyze_asset(asset_id)
                    total_processed += 1
                    
                    # Emit progress
                    socketio.emit('analysis_progress', {
                        'processed': total_processed,
                        'current_asset': asset_id,
                        'result': result
                    })
                    
                    # Small delay to prevent overwhelming
                    time.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error processing asset: {e}")
            
            page += 1
        
        socketio.emit('analysis_status', {
            'status': 'completed', 
            'total_processed': total_processed
        })
        logger.info(f"Batch analysis completed. Processed {total_processed} assets")
        
    except Exception as e:
        logger.error(f"Error in batch analysis: {e}")
        socketio.emit('analysis_status', {'status': 'error', 'error': str(e)})

@app.route('/api/results')
def get_results():
    """Get analysis results"""
    try:
        category = request.args.get('category', 'all')
        
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            if category == 'screenshots':
                cursor = conn.execute('''
                    SELECT * FROM asset_analysis 
                    WHERE is_screenshot = 1 
                    ORDER BY confidence_score DESC 
                    LIMIT 1000
                ''')
            elif category == 'web_cache':
                cursor = conn.execute('''
                    SELECT * FROM asset_analysis 
                    WHERE is_web_cache = 1 
                    ORDER BY confidence_score DESC 
                    LIMIT 1000
                ''')
            elif category == 'low_quality':
                cursor = conn.execute('''
                    SELECT * FROM asset_analysis 
                    WHERE is_low_quality = 1 
                    ORDER BY confidence_score DESC 
                    LIMIT 1000
                ''')
            elif category == 'high_confidence':
                cursor = conn.execute('''
                    SELECT * FROM asset_analysis 
                    WHERE confidence_score > 0.7 
                    ORDER BY confidence_score DESC 
                    LIMIT 1000
                ''')
            else:  # all
                cursor = conn.execute('''
                    SELECT * FROM asset_analysis 
                    ORDER BY confidence_score DESC 
                    LIMIT 1000
                ''')
            
            columns = [description[0] for description in cursor.description]
            results = []
            
            for row in cursor.fetchall():
                result = dict(zip(columns, row))
                # Parse JSON fields
                result['flags'] = json.loads(result['flags']) if result['flags'] else []
                result['recommendations'] = json.loads(result['recommendations']) if result['recommendations'] else []
                results.append(result)
            
            return jsonify(results)
            
    except Exception as e:
        logger.error(f"Error fetching results: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/statistics')
def get_statistics():
    """Get analysis statistics"""
    try:
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            # Total analyzed
            total = conn.execute('SELECT COUNT(*) FROM asset_analysis').fetchone()[0]
            
            # By category
            screenshots = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE is_screenshot = 1').fetchone()[0]
            web_cache = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE is_web_cache = 1').fetchone()[0]
            low_quality = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE is_low_quality = 1').fetchone()[0]
            corrupt = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE is_corrupt = 1').fetchone()[0]
            
            # High confidence unwanted
            high_confidence = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE confidence_score > 0.7').fetchone()[0]
            
            # Marked for deletion
            marked_deletion = conn.execute('SELECT COUNT(*) FROM asset_analysis WHERE marked_for_deletion = 1').fetchone()[0]
            
            return jsonify({
                'total_analyzed': total,
                'screenshots': screenshots,
                'web_cache': web_cache,
                'low_quality': low_quality,
                'corrupt': corrupt,
                'high_confidence_unwanted': high_confidence,
                'marked_for_deletion': marked_deletion
            })
            
    except Exception as e:
        logger.error(f"Error fetching statistics: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/mark_for_deletion', methods=['POST'])
def mark_for_deletion():
    """Mark assets for deletion"""
    try:
        data = request.json
        asset_ids = data.get('asset_ids', [])
        
        with sqlite3.connect(cleaner_engine.db_path) as conn:
            for asset_id in asset_ids:
                conn.execute(
                    'UPDATE asset_analysis SET marked_for_deletion = 1 WHERE asset_id = ?',
                    (asset_id,)
                )
        
        return jsonify({'success': True, 'marked_count': len(asset_ids)})
        
    except Exception as e:
        logger.error(f"Error marking for deletion: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    # Create data directory
    os.makedirs('/app/data', exist_ok=True)
    
    # Load config if exists
    try:
        with open('/app/data/config.json', 'r') as f:
            config = json.load(f)
            immich_client = ImmichAPIClient(config['immich_url'], config['api_key'])
            cleaner_engine = ImageCleanerEngine(immich_client)
            logger.info("Loaded existing configuration")
    except:
        logger.info("No existing configuration found")
    
    # Run the app
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

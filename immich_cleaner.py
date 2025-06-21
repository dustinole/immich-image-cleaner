import os
import sqlite3
import requests
import json
import csv
from datetime import datetime
import re
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ImmichCleaner:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.db_path = '/data/cleaner.db'
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for storing results"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cleanup_candidates (
                id TEXT PRIMARY KEY,
                filename TEXT,
                original_path TEXT,
                file_size INTEGER,
                created_at TEXT,
                category TEXT,
                detection_reason TEXT,
                marked_for_deletion BOOLEAN DEFAULT 0,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def analyze_asset(self, asset):
        """Analyze a single asset to determine if it's a cleanup candidate"""
        try:
            filename = asset.get('originalFileName', '').lower()
            original_path = asset.get('originalPath', '')
            file_size = asset.get('exifInfo', {}).get('fileSizeInByte', 0)
            asset_id = asset.get('id', '')
            created_at = asset.get('fileCreatedAt', '')
            
            category = None
            reason = None
            
            # Check for screenshots - EXPANDED PATTERNS
            screenshot_patterns = [
                # Standard screenshot patterns
                r'screenshot[-_\s]?\d*',
                r'screen[-_\s]?shot',
                r'screen[-_\s]?capture',
                r'scr[-_]?\d+',
                r'capture[-_]?\d*',
                
                # Mobile screenshot patterns
                r'^img[-_]?\d+[-_]\d+',  # IMG_20210109_095536
                r'^\d{4}-\d{2}-\d{2}[-_]\d{2}-\d{2}-\d{2}',  # 2021-01-09-09-55-36
                r'^\d{4}-\d{2}-\d{2}[-_]at[-_]\d{2}\.\d{2}\.\d{2}',  # 2021-01-09 at 09.55.36
                r'^photo[-_]?\d{4}-\d{2}-\d{2}',  # Photo 2021-01-09
                
                # Device-specific patterns
                r'^img[-_]?\d{8}[-_]\d{6}',  # img_20210109_095536
                r'^signal-\d{4}-\d{2}-\d{2}',  # Signal screenshots
                r'^whatsapp[-_]image',  # WhatsApp screenshots
                r'^telegram[-_]image',  # Telegram screenshots
                r'^photo_\d{4}-\d{2}-\d{2}',  # Various photo apps
                r'^pxl_\d{8}_\d{6}',  # Pixel phone pattern
                
                # Windows/Desktop patterns
                r'^snip[-_]?\d*',  # Snipping tool
                r'^greenshot[-_]',  # Greenshot tool
                r'^capture\d{4}',  # Generic capture
                r'^clip[-_]?\d+',  # Clipboard saves
                
                # More generic patterns
                r'[-_]screenshot[-_]',  # Screenshot anywhere in name
                r'\.screenshot\.',  # .screenshot.
                r'^ss[-_]?\d+',  # ss_001, SS-123
                r'^snap[-_]?\d+',  # snap_001
                r'^grab[-_]?\d+',  # grab_001
            ]
            
            for pattern in screenshot_patterns:
                if re.search(pattern, filename):
                    category = 'screenshot'
                    reason = f'Filename matches pattern: {pattern}'
                    break
            
            # Check for web/cache files
            if not category:
                web_patterns = [
                    r'\.webp
    
    def save_candidate(self, asset_id, filename, original_path, file_size, 
                      created_at, category, reason):
        """Save cleanup candidate to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO cleanup_candidates 
            (id, filename, original_path, file_size, created_at, category, detection_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (asset_id, filename, original_path, file_size, created_at, category, reason))
        
        conn.commit()
        conn.close()
    
    def get_results(self):
        """Get analysis results from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        results = {
            'screenshots': [],
            'web_files': [],
            'recovery_artifacts': []
        }
        
        # Get screenshots
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'screenshot'
            ORDER BY created_at DESC
        ''')
        results['screenshots'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                 for row in cursor.fetchall()]
        
        # Get web files
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'web_file'
            ORDER BY created_at DESC
        ''')
        results['web_files'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                               for row in cursor.fetchall()]
        
        # Get recovery artifacts
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'recovery_artifact'
            ORDER BY created_at DESC
        ''')
        results['recovery_artifacts'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                        for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_statistics(self):
        """Get analysis statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total analyzed (this would need to be tracked separately in a real implementation)
        cursor.execute('SELECT COUNT(DISTINCT id) FROM cleanup_candidates')
        stats['total_analyzed'] = cursor.fetchone()[0]
        
        # Category counts
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'screenshot'")
        stats['screenshots_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'web_file'")
        stats['web_files_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'recovery_artifact'")
        stats['recovery_artifacts_found'] = cursor.fetchone()[0]
        
        # Total size
        cursor.execute("SELECT SUM(file_size) FROM cleanup_candidates")
        total_bytes = cursor.fetchone()[0] or 0
        stats['total_size_mb'] = round(total_bytes / 1024 / 1024, 2)
        
        # Marked for deletion
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE marked_for_deletion = 1")
        stats['marked_for_deletion'] = cursor.fetchone()[0]
        
        conn.close()
        return stats
    
    def mark_for_deletion(self, asset_ids, mark=True):
        """Mark assets for deletion"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for asset_id in asset_ids:
            cursor.execute('''
                UPDATE cleanup_candidates 
                SET marked_for_deletion = ? 
                WHERE id = ?
            ''', (1 if mark else 0, asset_id))
        
        conn.commit()
        conn.close()
    
    def export_to_csv(self):
        """Export results to CSV file"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        csv_path = '/data/cleanup_results.csv'
        
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['ID', 'Filename', 'Path', 'Size (bytes)', 'Created', 'Category', 'Reason', 'Marked for Deletion'])
            
            cursor.execute('''
                SELECT id, filename, original_path, file_size, created_at, category, detection_reason, marked_for_deletion
                FROM cleanup_candidates
                ORDER BY category, created_at DESC
            ''')
            
            for row in cursor.fetchall():
                writer.writerow(row)
        
        conn.close()
        return csv_path
    
    def generate_deletion_script(self):
        """Generate a script to delete marked assets"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        script_path = '/data/delete_assets.sh'
        
        with open(script_path, 'w') as f:
            f.write('#!/bin/bash\n\n')
            f.write('# Immich Asset Deletion Script\n')
            f.write(f'# Generated on {datetime.now().isoformat()}\n')
            f.write(f'# API URL: {self.base_url}\n\n')
            f.write('# This script will delete the marked assets via Immich API\n\n')
            
            cursor.execute('''
                SELECT id, filename 
                FROM cleanup_candidates 
                WHERE marked_for_deletion = 1
            ''')
            
            for asset_id, filename in cursor.fetchall():
                f.write(f'# Deleting: {filename}\n')
                f.write(f'curl -X DELETE "{self.base_url}/api/asset" \\\n')
                f.write(f'  -H "X-Api-Key: {self.api_key}" \\\n')
                f.write(f'  -H "Content-Type: application/json" \\\n')
                f.write(f'  -d \'{{"ids":["{asset_id}"],"force":true}}\'\n\n')
        
        # Make script executable
        os.chmod(script_path, 0o755)
        
        conn.close()
    def remove_deleted_assets(self, asset_ids):
        """Remove deleted assets from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(asset_ids))
        cursor.execute(f'''
            DELETE FROM cleanup_candidates 
            WHERE id IN ({placeholders})
        ''', asset_ids)
        
        conn.commit()
        conn.close(),
                    r'cache',
                    r'temp[-_]',
                    r'tmp[-_]',
                    r'download[s]?[-_]?\d*',
                    r'[-_]download\.',
                    r'facebook[-_]',
                    r'fb[-_]img',
                    r'whatsapp[-_]',
                    r'instagram[-_]',
                    r'twitter[-_]',
                    r'reddit[-_]',
                    r'tumblr[-_]',
                    r'pinterest[-_]',
                    r'messenger[-_]',
                    r'discord[-_]',
                    r'slack[-_]',
                ]
                
                for pattern in web_patterns:
                    if re.search(pattern, filename):
                        category = 'web_file'
                        reason = f'Filename matches pattern: {pattern}'
                        break
            
            # Check for recovery artifacts
            if not category:
                recovery_patterns = [
                    r'^recovered[-_]',
                    r'^found\.\d+',
                    r'^file\d+',
                    r'^copy[-_]?of[-_]',
                    r'\(\d+\)\.',  # Files with (1), (2) etc
                    r'^duplicate[-_]',
                    r'^untitled[-_]?\d*',
                    r'^noname',
                    r'^image\d+',
                    r'^photo\d+',
                    r'^picture\d+',
                    r'^img\d+',
                    r'^dsc[-_]?\d+',
                    r'^dcim[-_]?\d+',
                    r'^burst\d+',  # Burst photo artifacts
                    r'^img_\d{4}
    
    def save_candidate(self, asset_id, filename, original_path, file_size, 
                      created_at, category, reason):
        """Save cleanup candidate to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO cleanup_candidates 
            (id, filename, original_path, file_size, created_at, category, detection_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (asset_id, filename, original_path, file_size, created_at, category, reason))
        
        conn.commit()
        conn.close()
    
    def get_results(self):
        """Get analysis results from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        results = {
            'screenshots': [],
            'web_files': [],
            'recovery_artifacts': []
        }
        
        # Get screenshots
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'screenshot'
            ORDER BY created_at DESC
        ''')
        results['screenshots'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                 for row in cursor.fetchall()]
        
        # Get web files
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'web_file'
            ORDER BY created_at DESC
        ''')
        results['web_files'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                               for row in cursor.fetchall()]
        
        # Get recovery artifacts
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'recovery_artifact'
            ORDER BY created_at DESC
        ''')
        results['recovery_artifacts'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                        for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_statistics(self):
        """Get analysis statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total analyzed (this would need to be tracked separately in a real implementation)
        cursor.execute('SELECT COUNT(DISTINCT id) FROM cleanup_candidates')
        stats['total_analyzed'] = cursor.fetchone()[0]
        
        # Category counts
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'screenshot'")
        stats['screenshots_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'web_file'")
        stats['web_files_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'recovery_artifact'")
        stats['recovery_artifacts_found'] = cursor.fetchone()[0]
        
        # Total size
        cursor.execute("SELECT SUM(file_size) FROM cleanup_candidates")
        total_bytes = cursor.fetchone()[0] or 0
        stats['total_size_mb'] = round(total_bytes / 1024 / 1024, 2)
        
        # Marked for deletion
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE marked_for_deletion = 1")
        stats['marked_for_deletion'] = cursor.fetchone()[0]
        
        conn.close()
        return stats
    
    def mark_for_deletion(self, asset_ids, mark=True):
        """Mark assets for deletion"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for asset_id in asset_ids:
            cursor.execute('''
                UPDATE cleanup_candidates 
                SET marked_for_deletion = ? 
                WHERE id = ?
            ''', (1 if mark else 0, asset_id))
        
        conn.commit()
        conn.close()
    
    def export_to_csv(self):
        """Export results to CSV file"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        csv_path = '/data/cleanup_results.csv'
        
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['ID', 'Filename', 'Path', 'Size (bytes)', 'Created', 'Category', 'Reason', 'Marked for Deletion'])
            
            cursor.execute('''
                SELECT id, filename, original_path, file_size, created_at, category, detection_reason, marked_for_deletion
                FROM cleanup_candidates
                ORDER BY category, created_at DESC
            ''')
            
            for row in cursor.fetchall():
                writer.writerow(row)
        
        conn.close()
        return csv_path
    
    def generate_deletion_script(self):
        """Generate a script to delete marked assets"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        script_path = '/data/delete_assets.sh'
        
        with open(script_path, 'w') as f:
            f.write('#!/bin/bash\n\n')
            f.write('# Immich Asset Deletion Script\n')
            f.write(f'# Generated on {datetime.now().isoformat()}\n')
            f.write(f'# API URL: {self.base_url}\n\n')
            f.write('# This script will delete the marked assets via Immich API\n\n')
            
            cursor.execute('''
                SELECT id, filename 
                FROM cleanup_candidates 
                WHERE marked_for_deletion = 1
            ''')
            
            for asset_id, filename in cursor.fetchall():
                f.write(f'# Deleting: {filename}\n')
                f.write(f'curl -X DELETE "{self.base_url}/api/asset" \\\n')
                f.write(f'  -H "X-Api-Key: {self.api_key}" \\\n')
                f.write(f'  -H "Content-Type: application/json" \\\n')
                f.write(f'  -d \'{{"ids":["{asset_id}"],"force":true}}\'\n\n')
        
        # Make script executable
        os.chmod(script_path, 0o755)
        
        conn.close()
        return script_path,  # IMG_0001 etc
                ]
                
                for pattern in recovery_patterns:
                    if re.search(pattern, filename):
                        category = 'recovery_artifact'
                        reason = f'Filename matches pattern: {pattern}'
                        break
            
            # If we found a match, save to database
            if category:
                self.save_candidate(asset_id, filename, original_path, file_size, 
                                  created_at, category, reason)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error analyzing asset {asset.get('id', 'unknown')}: {e}")
            return False
    
    def save_candidate(self, asset_id, filename, original_path, file_size, 
                      created_at, category, reason):
        """Save cleanup candidate to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO cleanup_candidates 
            (id, filename, original_path, file_size, created_at, category, detection_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (asset_id, filename, original_path, file_size, created_at, category, reason))
        
        conn.commit()
        conn.close()
    
    def get_results(self):
        """Get analysis results from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        results = {
            'screenshots': [],
            'web_files': [],
            'recovery_artifacts': []
        }
        
        # Get screenshots
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'screenshot'
            ORDER BY created_at DESC
        ''')
        results['screenshots'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                 for row in cursor.fetchall()]
        
        # Get web files
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'web_file'
            ORDER BY created_at DESC
        ''')
        results['web_files'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                               for row in cursor.fetchall()]
        
        # Get recovery artifacts
        cursor.execute('''
            SELECT id, filename, original_path, file_size, created_at, detection_reason, marked_for_deletion
            FROM cleanup_candidates
            WHERE category = 'recovery_artifact'
            ORDER BY created_at DESC
        ''')
        results['recovery_artifacts'] = [dict(zip(['id', 'filename', 'path', 'size', 'date', 'reason', 'marked'], row)) 
                                        for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_statistics(self):
        """Get analysis statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total analyzed (this would need to be tracked separately in a real implementation)
        cursor.execute('SELECT COUNT(DISTINCT id) FROM cleanup_candidates')
        stats['total_analyzed'] = cursor.fetchone()[0]
        
        # Category counts
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'screenshot'")
        stats['screenshots_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'web_file'")
        stats['web_files_found'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE category = 'recovery_artifact'")
        stats['recovery_artifacts_found'] = cursor.fetchone()[0]
        
        # Total size
        cursor.execute("SELECT SUM(file_size) FROM cleanup_candidates")
        total_bytes = cursor.fetchone()[0] or 0
        stats['total_size_mb'] = round(total_bytes / 1024 / 1024, 2)
        
        # Marked for deletion
        cursor.execute("SELECT COUNT(*) FROM cleanup_candidates WHERE marked_for_deletion = 1")
        stats['marked_for_deletion'] = cursor.fetchone()[0]
        
        conn.close()
        return stats
    
    def mark_for_deletion(self, asset_ids, mark=True):
        """Mark assets for deletion"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for asset_id in asset_ids:
            cursor.execute('''
                UPDATE cleanup_candidates 
                SET marked_for_deletion = ? 
                WHERE id = ?
            ''', (1 if mark else 0, asset_id))
        
        conn.commit()
        conn.close()
    
    def export_to_csv(self):
        """Export results to CSV file"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        csv_path = '/data/cleanup_results.csv'
        
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['ID', 'Filename', 'Path', 'Size (bytes)', 'Created', 'Category', 'Reason', 'Marked for Deletion'])
            
            cursor.execute('''
                SELECT id, filename, original_path, file_size, created_at, category, detection_reason, marked_for_deletion
                FROM cleanup_candidates
                ORDER BY category, created_at DESC
            ''')
            
            for row in cursor.fetchall():
                writer.writerow(row)
        
        conn.close()
        return csv_path
    
    def generate_deletion_script(self):
        """Generate a script to delete marked assets"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        script_path = '/data/delete_assets.sh'
        
        with open(script_path, 'w') as f:
            f.write('#!/bin/bash\n\n')
            f.write('# Immich Asset Deletion Script\n')
            f.write(f'# Generated on {datetime.now().isoformat()}\n')
            f.write(f'# API URL: {self.base_url}\n\n')
            f.write('# This script will delete the marked assets via Immich API\n\n')
            
            cursor.execute('''
                SELECT id, filename 
                FROM cleanup_candidates 
                WHERE marked_for_deletion = 1
            ''')
            
            for asset_id, filename in cursor.fetchall():
                f.write(f'# Deleting: {filename}\n')
                f.write(f'curl -X DELETE "{self.base_url}/api/asset" \\\n')
                f.write(f'  -H "X-Api-Key: {self.api_key}" \\\n')
                f.write(f'  -H "Content-Type: application/json" \\\n')
                f.write(f'  -d \'{{"ids":["{asset_id}"],"force":true}}\'\n\n')
        
        # Make script executable
        os.chmod(script_path, 0o755)
        
        conn.close()
        return script_path

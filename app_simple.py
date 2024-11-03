from flask import Flask, render_template, request, Response, stream_with_context
import requests
import json
from datetime import datetime
import logging
import re
import mimetypes
import gzip
import io
import email
from functools import lru_cache

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@lru_cache(maxsize=100)
def get_available_indexes():
    """Get list of available Common Crawl indexes"""
    try:
        response = requests.get(
            "https://index.commoncrawl.org/collinfo.json",
            timeout=10,
            headers={'User-Agent': 'CommonCrawlSearch/1.0'}
        )
        if response.status_code == 200:
            indexes = response.json()
            return [index['cdx-api'] for index in sorted(indexes, key=lambda x: x['id'], reverse=True)]
    except Exception as e:
        logger.error(f"Error fetching indexes: {str(e)}")
        return ["https://index.commoncrawl.org/CC-MAIN-2024-04-index"]

@lru_cache(maxsize=100)
def search_common_crawl(url):
    """Search for URL in Common Crawl indexes using binary search"""
    indexes = get_available_indexes()
    if not indexes:
        return None

    domain = re.match(r'^https?://(www\.)?([^/]+)', url)
    domain = domain.group(2) if domain else url.split('/')[0].strip()
    
    # Binary search for domain
    found_index = None
    for domain_variant in [domain, f"www.{domain}"]:
        left = 0
        right = len(indexes) - 1
        
        while left <= right:
            mid = (left + right) // 2
            current_index = indexes[mid]
            
            try:
                response = requests.get(
                    current_index,
                    params={'url': domain_variant, 'output': 'json', 'matchType': 'domain'},
                    timeout=5,
                    headers={'User-Agent': 'CommonCrawlSearch/1.0'}
                )
                if response.status_code == 200 and response.text.strip():
                    found_index = current_index
                    right = mid - 1  # Keep searching newer indexes
                else:
                    left = mid + 1
            except Exception as e:
                logger.warning(f"Error checking index {current_index}: {str(e)}")
                left = mid + 1
        
        if found_index:
            break

    if not found_index:
        return None

    # Search for exact URL in found index and newer indexes
    start_index = indexes.index(found_index)
    url_variations = [
        url,
        url.replace('http://', 'https://'),
        url.replace('https://', 'http://'),
        url.replace('://www.', '://'),
        url.replace('://', '://www.')
    ]

    # Search through the found index and all newer indexes
    for index in indexes[start_index::-1]:  # Go backwards through newer indexes
        for url_variant in url_variations:
            try:
                response = requests.get(
                    index,
                    params={'url': url_variant, 'output': 'json', 'matchType': 'exact'},
                    timeout=5,
                    headers={'User-Agent': 'CommonCrawlSearch/1.0'}
                )
                if response.status_code == 200 and response.text.strip():
                    results = [json.loads(line) for line in response.text.strip().split('\n')]
                    if results:
                        results.sort(key=lambda x: x['timestamp'], reverse=True)
                        return results[0]
            except Exception as e:
                logger.warning(f"Error checking URL variant: {str(e)}")

    return None

def normalize_url(url):
    """Normalize URL for searching"""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + ('www.' if not url.startswith('www.') else '') + url
    return url.rstrip('/')

def format_timestamp(timestamp):
    """Format timestamp for display"""
    try:
        return datetime.strptime(timestamp, '%Y%m%d%H%M%S').strftime('%B %d, %Y at %I:%M:%S %p')
    except Exception:
        return timestamp

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method != 'POST':
        return render_template('index.html')

    url = request.form.get('url')
    if not url:
        return render_template('index.html')

    result = search_common_crawl(normalize_url(url))
    if not result:
        return render_template('index.html', url=url)

    return render_template('index.html',
        result=result,
        url=url,
        formatted_timestamp=format_timestamp(result['timestamp']),
        crawl_index=result['filename'].split('/')[1] if result.get('filename') else None,
        additional_info={
            'status_code': result.get('status', 'N/A'),
            'mime_type': result.get('mime', 'N/A'),
            'length': result.get('length', 'N/A'),
            'offset': result.get('offset', 'N/A'),
            'filename': result.get('filename', 'N/A'),
            'languages': result.get('languages', 'N/A'),
            'charset': result.get('charset', 'N/A'),
            'digest': result.get('digest', 'N/A')
        }
    )

@app.route('/search-progress')
def search_progress():
    @stream_with_context
    def generate():
        url = request.args.get('url')
        if not url:
            yield f"data: {json.dumps({'status': 'Error: No URL provided', 'progress': 100, 'complete': True})}\n\n"
            return

        normalized_url = normalize_url(url)
        result = search_common_crawl(normalized_url)
        
        if result:
            crawl_id = re.search(r'CC-MAIN-\d{4}-\d{2}', result['filename'])
            crawl_label = crawl_id.group(0) if crawl_id else 'Unknown Crawl'
            yield f"data: {json.dumps({'status': f'Found exact match in {crawl_label}! Loading content...', 'progress': 100, 'complete': True})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'URL not found', 'progress': 100, 'complete': True})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/download-file')
def download_file():
    url = request.args.get('url')
    result = search_common_crawl(normalize_url(url))
    
    if not result or not all(result.get(k) for k in ['filename', 'offset', 'length']):
        return 'File not found', 404

    try:
        response = requests.get(
            f"https://data.commoncrawl.org/{result['filename']}",
            headers={'Range': f"bytes={result['offset']}-{int(result['offset'])+int(result['length'])-1}"},
            stream=True
        )
        
        if response.status_code not in [200, 206]:
            return 'File not found', 404

        raw_data = gzip.GzipFile(fileobj=io.BytesIO(response.content)) if result['filename'].endswith('.gz') else io.BytesIO(response.content)
        warc_data = raw_data.read()
        parts = warc_data.split(b'\r\n\r\n', 2)
        
        if len(parts) < 3:
            return 'Error processing file', 500

        http_response = email.message_from_string(parts[1].decode('utf-8', errors='ignore'))
        content = parts[2]
        
        mime_type = http_response.get_content_type() or result.get('mime', 'application/octet-stream')
        ext = mimetypes.guess_extension(mime_type, strict=False) or '.txt'
        
        filename = url.split('/')[-1].split('?')[0] or 'archived_file'
        if not filename.endswith(ext):
            filename = f"{filename.rsplit('.', 1)[0] if '.' in filename else filename}{ext}"

        return Response(
            content,
            mimetype=mime_type,
            headers={
                'Content-Disposition': f'attachment;filename={filename}',
                'Content-Length': str(len(content))
            }
        )

    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return f"Error downloading file: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
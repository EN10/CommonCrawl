from flask import Flask, render_template, request, Response, stream_with_context
import requests
import json
from datetime import datetime
import logging
import time
from warcio.archiveiterator import ArchiveIterator
from urllib.parse import urljoin
import io
import gzip
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_available_indexes():
    """Get list of available Common Crawl indexes"""
    try:
        response = requests.get("https://index.commoncrawl.org/collinfo.json")
        if response.status_code == 200:
            indexes = response.json()
            # Sort by timestamp in descending order (newest first)
            return [index['cdx-api'] for index in sorted(indexes, key=lambda x: x['id'], reverse=True)]
    except Exception as e:
        logger.error(f"Error fetching Common Crawl indexes: {str(e)}", exc_info=True)
        return ["https://index.commoncrawl.org/CC-MAIN-2024-04-index"]

def binary_search_indexes(url, indexes):
    """Binary search through indexes to find the most recent capture of the URL"""
    logger.debug(f"Starting binary search for URL: {url} across {len(indexes)} indexes")
    
    params = {
        'url': url,
        'output': 'json',
        'showNumPages': False,
        'matchType': 'exact'  # Use exact matching for more precise results
    }

    left = 0
    right = len(indexes) - 1
    last_found_result = None
    last_found_index = None

    while left <= right:
        mid = (left + right) // 2
        mid_index = indexes[mid]
        
        try:
            logger.debug(f"Trying index: {mid_index} (position {mid})")
            response = requests.get(mid_index, params=params)
            
            if response.status_code == 200 and response.text.strip():
                results = [json.loads(line) for line in response.text.strip().split('\n')]
                if results:
                    results.sort(key=lambda x: x['timestamp'], reverse=True)
                    last_found_result = results[0]
                    last_found_index = mid
                    right = mid - 1
                else:
                    left = mid + 1
            else:
                left = mid + 1
            
            time.sleep(0.2)
            
        except Exception as e:
            logger.error(f"Error searching index {mid_index}: {str(e)}", exc_info=True)
            left = mid + 1
            continue

    return last_found_result

def binary_search_domain(domain, indexes):
    """Binary search to find the domain in indexes"""
    logger.debug(f"Starting binary search for domain: {domain}")
    
    params = {
        'url': domain,
        'output': 'json',
        'showNumPages': False,
        'matchType': 'domain'
    }

    left = 0
    right = len(indexes) - 1
    last_found_index = None
    timeout = 2  # 2 second timeout for each request

    while left <= right:
        mid = (left + right) // 2
        mid_index = indexes[mid]
        
        try:
            logger.debug(f"Trying index: {mid_index} (position {mid})")
            response = requests.get(mid_index, params=params, timeout=timeout)
            
            if response.status_code == 200 and response.text.strip():
                last_found_index = mid_index
                right = mid - 1  # Keep searching newer indexes
            else:
                left = mid + 1
            
            time.sleep(0.1)  # Reduced delay
            
        except requests.Timeout:
            logger.warning(f"Timeout searching index {mid_index}, moving to next")
            left = mid + 1
            continue
        except Exception as e:
            logger.error(f"Error searching index {mid_index}: {str(e)}", exc_info=True)
            left = mid + 1
            continue

    return last_found_index

def linear_search_url(url, index):
    """Linear search for exact URL match within an index"""
    logger.debug(f"Linear searching for URL: {url} in index: {index}")
    
    params = {
        'url': url,
        'output': 'json',
        'showNumPages': False,
        'matchType': 'exact'
    }

    try:
        response = requests.get(index, params=params, timeout=2)
        if response.status_code == 200 and response.text.strip():
            results = [json.loads(line) for line in response.text.strip().split('\n')]
            if results:
                # Sort by timestamp in descending order
                results.sort(key=lambda x: x['timestamp'], reverse=True)
                logger.info(f"Found {len(results)} matches for URL {url}")
                return results[0]  # Return the most recent match
    except requests.Timeout:
        logger.warning(f"Timeout during linear search for {url}")
    except Exception as e:
        logger.error(f"Error in linear search: {str(e)}", exc_info=True)
    
    return None

def search_common_crawl(url):
    """Two-step search: binary search for domain, then linear search for full URL"""
    logger.debug(f"Starting search for URL: {url}")
    
    # Get all available indexes
    indexes = get_available_indexes()
    if not indexes:
        logger.error("No Common Crawl indexes available")
        return None

    # Extract domain from URL
    domain_match = re.match(r'^https?://(www\.)?([^/]+)', url)
    if domain_match:
        base_domain = domain_match.group(2)
    else:
        base_domain = url.split('/')[0].strip()
    
    logger.debug(f"Starting binary search for domain: {base_domain}")
    
    # Step 1: Binary search for the domain
    domain_variations = [
        base_domain,
        f"www.{base_domain}"
    ]

    found_index = None
    for domain in domain_variations:
        # Binary search for domain
        left = 0
        right = len(indexes) - 1
        
        while left <= right:
            mid = (left + right) // 2
            current_index = indexes[mid]
            
            try:
                logger.debug(f"Checking domain '{domain}' in index: {current_index}")
                params = {
                    'url': domain,
                    'output': 'json',
                    'matchType': 'domain'
                }
                
                response = requests.get(current_index, params=params, timeout=2)
                if response.status_code == 200 and response.text.strip():
                    found_index = current_index
                    right = mid - 1  # Keep searching newer indexes
                else:
                    left = mid + 1
                
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Error checking domain in index {current_index}: {str(e)}")
                left = mid + 1
                continue
        
        if found_index:
            logger.info(f"Found domain '{domain}' in index: {found_index}")
            break

    if not found_index:
        logger.warning(f"Domain not found in any index: {base_domain}")
        return None

    # Step 2: Linear search for the exact URL in the found index and newer indexes
    logger.debug(f"Starting linear search for full URL from found index")
    start_index = indexes.index(found_index)
    
    # Search through the found index and all newer indexes
    for index in indexes[start_index::-1]:  # Go backwards through newer indexes
        try:
            logger.debug(f"Checking URL in index: {index}")
            url_variations = [
                url.replace('?ver=3.8.1', ''),  # Remove version
                url.replace('http://', 'https://'),
                url.replace('https://', 'http://'),
                url.replace('://www.', '://'),
                url.replace('://', '://www.')
            ]

            for url_variant in url_variations:
                params = {
                    'url': url_variant,
                    'output': 'json',
                    'matchType': 'exact'
                }
                
                response = requests.get(index, params=params, timeout=2)
                if response.status_code == 200 and response.text.strip():
                    results = [json.loads(line) for line in response.text.strip().split('\n')]
                    if results:
                        results.sort(key=lambda x: x['timestamp'], reverse=True)
                        logger.info(f"Found exact match for URL variant: {url_variant}")
                        return results[0]
                
                time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Error checking URL in index {index}: {str(e)}")
            continue

    logger.warning(f"No exact URL match found after searching from index {found_index}")
    return None

def fetch_wayback_content(wayback_url):
    logger.debug(f"Fetching content from Wayback Machine: {wayback_url}")
    try:
        response = requests.get(wayback_url)
        logger.debug(f"Wayback Machine response status: {response.status_code}")
        
        if response.status_code == 200:
            logger.debug("Successfully retrieved content from Wayback Machine")
            return response.text
        else:
            logger.warning(f"Failed to fetch content. Status code: {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching content: {str(e)}", exc_info=True)
    return None

def normalize_url(url):
    """Normalize URL for Common Crawl search"""
    logger.debug(f"Normalizing URL: {url}")
    
    # Clean the URL first
    url = url.strip()
    
    # Remove query parameters that might affect caching/versioning
    url = re.sub(r'\?(?:ver|rev)=[^&]*', '', url)
    
    # Handle protocol
    if not url.startswith(('http://', 'https://')):
        if not url.startswith('www.'):
            url = 'www.' + url
        url = 'https://' + url
    
    # Remove double slashes (except after protocol)
    url = re.sub(r'(?<!:)//+', '/', url)
    
    # Remove trailing slash
    url = url.rstrip('/')
    
    logger.debug(f"Normalized URL: {url}")
    return url

def format_timestamp(timestamp):
    """Convert Common Crawl timestamp to readable format"""
    try:
        # Common Crawl timestamp format: YYYYMMDDHHMMSS
        dt = datetime.strptime(timestamp, '%Y%m%d%H%M%S')
        return dt.strftime('%B %d, %Y at %I:%M:%S %p')
    except Exception as e:
        logger.error(f"Error formatting timestamp {timestamp}: {str(e)}")
        return timestamp

def fetch_common_crawl_content(result):
    """Fetch content directly from Common Crawl WARC file"""
    try:
        filename = result['filename']
        offset = int(result['offset'])
        length = int(result['length'])
        
        s3_url = f"https://data.commoncrawl.org/{filename}"
        headers = {'Range': f'bytes={offset}-{offset+length-1}'}
        response = requests.get(s3_url, headers=headers, stream=True)
        
        if response.status_code == 206:
            stream = io.BytesIO(response.content)
            for record in ArchiveIterator(stream):
                if record.rec_type == 'response':
                    http_headers = record.http_headers
                    if http_headers is None:
                        continue
                    
                    content = record.content_stream().read().decode('utf-8', errors='ignore')
                    base_url = result['url']

                    # Fix relative URLs in the content
                    def fix_url(url_str):
                        if url_str.startswith('//'):
                            return 'https:' + url_str
                        elif url_str.startswith('/'):
                            return urljoin(base_url, url_str)
                        elif not url_str.startswith(('http://', 'https://', 'data:', '#', 'mailto:')):
                            return urljoin(base_url, url_str)
                        return url_str

                    # Process all URLs in the content
                    def process_urls(match):
                        quote_char = match.group(1) or ''
                        url = match.group(2)
                        fixed_url = fix_url(url)
                        return f'/asset?url={fixed_url}'

                    # Replace URLs in different contexts
                    content = re.sub(r'src=(["\']?)([^"\'\s>]+)', lambda m: f'src={m.group(1)}/asset?url={fix_url(m.group(2))}', content)
                    content = re.sub(r'href=(["\']?)([^"\'\s>]+)', lambda m: f'href={m.group(1)}/asset?url={fix_url(m.group(2))}', content)
                    content = re.sub(r'url\((["\']?)([^"\'\)]+)(["\']?)\)', lambda m: f'url({m.group(1)}/asset?url={fix_url(m.group(2))}{m.group(3)})', content)

                    logger.debug("Successfully extracted and processed HTML content")
                    return content
                    
        logger.error(f"Failed to fetch WARC content. Status code: {response.status_code}")
        return None
        
    except Exception as e:
        logger.error(f"Error fetching Common Crawl content: {str(e)}", exc_info=True)
        return None

def fetch_asset_from_common_crawl(url):
    """Fetch assets (CSS, JS, images) from Common Crawl"""
    try:
        logger.debug(f"Searching for asset in Common Crawl: {url}")
        result = search_common_crawl(url)
        
        if not result:
            logger.warning(f"Asset not found in Common Crawl index: {url}")
            return None, None

        logger.debug(f"Found asset in Common Crawl: {result['filename']}")
        filename = result['filename']
        offset = int(result['offset'])
        length = int(result['length'])
        
        s3_url = f"https://data.commoncrawl.org/{filename}"
        headers = {'Range': f'bytes={offset}-{offset+length-1}'}
        
        logger.debug(f"Fetching asset from S3: {s3_url}")
        response = requests.get(s3_url, headers=headers, stream=True)
        
        if response.status_code == 206:
            stream = io.BytesIO(response.content)
            for record in ArchiveIterator(stream):
                if record.rec_type == 'response':
                    http_headers = record.http_headers
                    content_type = http_headers.get_header('Content-Type', '') if http_headers else None
                    
                    content = record.content_stream().read()
                    logger.info(f"Successfully fetched asset: {url} ({content_type})")
                    return content, content_type
            
            logger.warning(f"No valid record found in WARC for: {url}")
        else:
            logger.error(f"Failed to fetch from S3. Status: {response.status_code}")
        
        return None, None
    except Exception as e:
        logger.error(f"Error fetching asset {url}: {str(e)}", exc_info=True)
        return None, None

# Add these helper functions for better URL handling and logging
def clean_asset_url(url):
    """Clean and normalize asset URLs"""
    try:
        url = url.strip()
        
        # Handle protocol-relative URLs
        if url.startswith('//'):
            url = 'https:' + url
            
        # Remove any duplicate http:// or https:// prefixes
        url = re.sub(r'https?://(?:https?://)+', 'https://', url)
        
        # Remove WordPress version parameters and other common cache busters
        url = re.sub(r'\?(?:ver|rev)=[^&]*', '', url)
        
        # Ensure www. prefix for consistency
        if not url.startswith(('http://', 'https://')):
            if not url.startswith('www.'):
                url = 'www.' + url
            url = 'https://' + url
        elif '://' in url and not url.split('://', 1)[1].startswith('www.'):
            url = url.replace('://', '://www.')
            
        logger.debug(f"Cleaned asset URL: {url}")
        return url
    except Exception as e:
        logger.error(f"Error cleaning asset URL {url}: {str(e)}")
        return url

# Update the serve_asset route with better error handling
@app.route('/asset')
def serve_asset():
    original_url = request.args.get('url', '')
    if not original_url:
        logger.error("No URL provided in asset request")
        return "Asset URL not provided", 400

    try:
        url = clean_asset_url(original_url)
        logger.info(f"Asset request - Original: {original_url} -> Cleaned: {url}")
        
        content, warc_content_type = fetch_asset_from_common_crawl(url)
        
        if content is not None:
            content_type = warc_content_type or 'application/octet-stream'
            
            # Determine content type from file extension if not provided
            if content_type == 'application/octet-stream':
                ext = url.split('.')[-1].lower() if '.' in url else ''
                content_types = {
                    'css': 'text/css',
                    'js': 'application/javascript',
                    'png': 'image/png',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'gif': 'image/gif',
                    'svg': 'image/svg+xml',
                    'webp': 'image/webp',
                    'ico': 'image/x-icon',
                    'woff': 'font/woff',
                    'woff2': 'font/woff2',
                    'ttf': 'font/ttf',
                    'eot': 'application/vnd.ms-fontobject'
                }
                content_type = content_types.get(ext, 'application/octet-stream')
            
            logger.info(f"Successfully serving asset {url} with type {content_type}")
            return Response(content, 
                          headers={
                              'Cache-Control': 'public, max-age=31536000',
                              'Content-Type': content_type
                          })
        
        # Try alternative URL variations if the asset wasn't found
        variations = [
            url.replace('www.', ''),  # Try without www
            url.replace('https://', 'http://'),  # Try HTTP
            re.sub(r'https?://(?:www\.)?', 'https://www.', url)  # Force www
        ]
        
        for variant in variations:
            logger.debug(f"Trying variant URL: {variant}")
            content, warc_content_type = fetch_asset_from_common_crawl(variant)
            if content is not None:
                logger.info(f"Found asset using variant URL: {variant}")
                return Response(content, 
                              headers={
                                  'Cache-Control': 'public, max-age=31536000',
                                  'Content-Type': warc_content_type or 'application/octet-stream'
                              })
        
        logger.warning(f"Asset not found after trying variations: {url}")
        return f"Asset not found: {url}", 404

    except Exception as e:
        logger.error(f"Error serving asset {original_url}: {str(e)}", exc_info=True)
        return f"Error serving asset: {str(e)}", 500

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    content = None
    url = None
    formatted_timestamp = None
    crawl_index = None
    
    if request.method == 'POST':
        url = request.form.get('url')
        logger.info(f"Received search request for URL: {url}")
        
        if url:
            try:
                normalized_url = normalize_url(url)
                logger.debug(f"Searching for normalized URL: {normalized_url}")
                
                result = search_common_crawl(normalized_url)
                if result:
                    logger.debug(f"Found result: {result}")
                    formatted_timestamp = format_timestamp(result['timestamp'])
                    if result.get('filename'):
                        parts = result['filename'].split('/')
                        if len(parts) > 2:
                            crawl_index = parts[1]
                    content = fetch_common_crawl_content(result)
                else:
                    logger.warning("No results found in Common Crawl")
            except Exception as e:
                logger.error(f"Error processing request: {str(e)}", exc_info=True)
    
    return render_template('index.html', 
                         result=result, 
                         content=content, 
                         url=url, 
                         formatted_timestamp=formatted_timestamp,
                         crawl_index=crawl_index)

@app.route('/search-progress')
def search_progress():
    @stream_with_context
    def generate():
        url = request.args.get('url')
        
        if not url:
            status_data = {'status': 'Error: No URL provided', 'progress': 100, 'complete': True}
            yield f"data: {json.dumps(status_data)}\n\n"
            return
            
        normalized_url = normalize_url(url)
        
        try:
            status_data = {'status': 'Fetching Common Crawl indexes...', 'progress': 10}
            yield f"data: {json.dumps(status_data)}\n\n"
            
            indexes = get_available_indexes()
            if not indexes:
                status_data = {'status': 'No indexes available', 'progress': 100, 'complete': True}
                yield f"data: {json.dumps(status_data)}\n\n"
                return
                
            total_indexes = len(indexes)
            status_data = {'status': f'Found {total_indexes} Common Crawl indexes...', 'progress': 20}
            yield f"data: {json.dumps(status_data)}\n\n"
            
            # Extract domain from URL
            domain_match = re.match(r'^https?://(www\.)?([^/]+)', normalized_url)
            if domain_match:
                base_domain = domain_match.group(2)
            else:
                base_domain = normalized_url.split('/')[0].strip()
            
            status_data = {'status': f'Binary searching for domain: {base_domain}...', 'progress': 30}
            yield f"data: {json.dumps(status_data)}\n\n"
            
            # Binary search for domain
            domain_variations = [base_domain, f"www.{base_domain}"]
            found_index = None
            
            for domain in domain_variations:
                left = 0
                right = len(indexes) - 1
                
                while left <= right:
                    mid = (left + right) // 2
                    current_index = indexes[mid]
                    
                    progress = 30 + int(((total_indexes - (right - left)) / total_indexes) * 40)
                    status_data = {
                        'status': f'Checking domain in index {mid + 1} of {total_indexes}...',
                        'progress': progress
                    }
                    yield f"data: {json.dumps(status_data)}\n\n"
                    
                    try:
                        params = {
                            'url': domain,
                            'output': 'json',
                            'matchType': 'domain'
                        }
                        
                        response = requests.get(current_index, params=params, timeout=2)
                        if response.status_code == 200 and response.text.strip():
                            found_index = current_index
                            right = mid - 1  # Keep searching newer indexes
                            status_data = {
                                'status': f'Found domain in index {mid + 1}, checking newer indexes...',
                                'progress': progress + 5
                            }
                            yield f"data: {json.dumps(status_data)}\n\n"
                        else:
                            left = mid + 1
                        
                        time.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"Error checking index {current_index}: {str(e)}")
                        left = mid + 1
                        continue
                
                if found_index:
                    break
            
            if not found_index:
                status_data = {
                    'status': 'Domain not found in any index',
                    'progress': 100,
                    'complete': True
                }
                yield f"data: {json.dumps(status_data)}\n\n"
                return
            
            # Search for exact URL in found index
            status_data = {
                'status': 'Found domain! Searching for exact URL...',
                'progress': 80
            }
            yield f"data: {json.dumps(status_data)}\n\n"
            
            url_variations = [
                normalized_url.replace('?ver=3.8.1', ''),
                normalized_url.replace('http://', 'https://'),
                normalized_url.replace('https://', 'http://'),
                normalized_url.replace('://www.', '://'),
                normalized_url.replace('://', '://www.')
            ]
            
            for url_variant in url_variations:
                try:
                    params = {
                        'url': url_variant,
                        'output': 'json',
                        'matchType': 'exact'
                    }
                    
                    response = requests.get(found_index, params=params, timeout=2)
                    if response.status_code == 200 and response.text.strip():
                        results = [json.loads(line) for line in response.text.strip().split('\n')]
                        if results:
                            status_data = {
                                'status': 'Found exact match! Loading content...',
                                'progress': 100,
                                'complete': True
                            }
                            yield f"data: {json.dumps(status_data)}\n\n"
                            return
                except Exception as e:
                    logger.warning(f"Error checking URL variant: {str(e)}")
                    continue
            
            status_data = {
                'status': 'URL not found in index',
                'progress': 100,
                'complete': True
            }
            yield f"data: {json.dumps(status_data)}\n\n"
            
        except Exception as e:
            logger.error(f"Error in search progress: {str(e)}")
            status_data = {
                'status': f'Error: {str(e)}',
                'progress': 100,
                'complete': True
            }
            yield f"data: {json.dumps(status_data)}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )

if __name__ == '__main__':
    app.run(debug=True)
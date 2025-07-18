import requests
import time
import os
from datetime import datetime

def ping_site(url):
    """Ping the website and return status code and response time."""
    try:
        start_time = time.time()
        response = requests.get(url, timeout=10)
        response_time = (time.time() - start_time) * 1000  # in milliseconds
        return response.status_code, response_time
    except requests.exceptions.RequestException as e:
        return str(e), 0

def main():
    # Get the site URL from environment variable or use localhost as default
    site_url = os.getenv('RENDER_SITE_URL', 'http://localhost:10000')
    
    # Add http:// if not present
    if not site_url.startswith(('http://', 'https://')):
        site_url = f'http://{site_url}'
    
    print(f"Starting keep-alive service for {site_url}")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            try:
                status, response_time = ping_site(site_url)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if isinstance(status, int):
                    print(f"{timestamp} - Status: {status} | Response Time: {response_time:.2f}ms")
                else:
                    print(f"{timestamp} - Error: {status}")
                
            except Exception as e:
                print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Error in ping_site: {str(e)}")
            
            # Wait for 1 minute (60 seconds) before next ping
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Error in sleep: {str(e)}")
            
    except KeyboardInterrupt:
        print("\nStopping keep-alive service...")

if __name__ == "__main__":
    main()

import base58
import json
import os
import time
import threading
import multiprocessing
from multiprocessing import Pool, cpu_count
from solders.keypair import Keypair
from github import Github, Auth
import signal
from dotenv import load_dotenv
from flask import Flask, Response, request, jsonify

# Load environment variables from .env file
load_dotenv()

# Disable SSL warnings for GitHub API
import urllib3
urllib3.disable_warnings()

# Global flag for clean shutdown
shutdown_flag = False
app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching for development

# Store found wallets
found_wallets = []
found_wallets_lock = threading.Lock()

# Global counter for wallet generation
wallet_counter = 0
counter_lock = threading.Lock()

# Keep-alive status
keep_alive_active = False
keep_alive_thread = None
keep_alive_stop = threading.Event()

def signal_handler(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    print("\n👋 Shutting down gracefully...")

# Configuration
TARGET_SUFFIX = "bonk"
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO_NAME = os.getenv('REPO_NAME', 'bonk-wallets')
FILE_NAME = "wallets.json"  # File to store wallets in the repo

class WalletGenerator:
    def __init__(self):
        self.github = None
        self.repo = None
        self.wallets = set()
        self.existing_wallets = []
        self.last_save = 0
        self.save_interval = 60  # Save to GitHub every 60 seconds
        self.initial_github_check_done = False
        self.start_time = time.time()
        self.last_print = time.time()
        self.print_interval = 30.0  # Print status every 30 seconds

    def print_status(self, force=False):
        current_time = time.time()
        time_since_last_print = current_time - self.last_print
        
        global wallet_counter
        with counter_lock:
            elapsed = current_time - self.start_time
            rate = wallet_counter / elapsed if elapsed > 0 else 0
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            print(f"[{timestamp}] 🔍 Total: {wallet_counter:,} wallets | "
                  f"Rate: {rate:,.0f} w/s | "
                  f"Found: {len(self.wallets):,}", flush=True)
            
            self.last_print = current_time

    def setup_github(self, test_only=False):
        """Initialize GitHub connection and get the repository.
        
        Args:
            test_only (bool): If True, only test the connection without loading wallets
        """
        if not GITHUB_TOKEN or GITHUB_TOKEN == "your_github_token_here":
            print("❌ GitHub token not set or using default value.")
            return False
            
        if len(GITHUB_TOKEN) < 40:  # GitHub tokens are usually 40+ characters
            print("❌ Invalid GitHub token: Token appears to be too short")
            return False
            
        try:
            if not self.github:
                print("🔑 Attempting to authenticate with GitHub...")
                auth = Auth.Token(GITHUB_TOKEN)
                self.github = Github(auth=auth)
                
                # Test the connection
                user = self.github.get_user()
                print(f"✅ Authenticated as GitHub user: {user.login}")
                
                # Get the repo
                print(f"🔍 Attempting to access repository: {REPO_NAME}")
                self.repo = self.github.get_repo(REPO_NAME)
                print(f"✅ Successfully connected to repository: {REPO_NAME}")
                
                if not test_only:
                    try:
                        contents = self.repo.get_contents(FILE_NAME)
                        print(f"📁 Found existing {FILE_NAME} in repository")
                    except:
                        print(f"ℹ️ {FILE_NAME} not found in repository. It will be created when the first wallet is found.")
            return True
                        
        except Exception as e:
            print(f"❌ Error connecting to GitHub: {str(e)}")
            if not test_only:
                print("Please verify:")
                print("1. Your GitHub token is correct and has 'repo' scope")
                print(f"2. The repository '{REPO_NAME}' exists and is accessible")
                print("3. The token has the correct permissions")
            self.repo = None
            return False

    def load_existing_wallets(self):
        """Load existing wallets from GitHub or create an empty list."""
        self.existing_wallets = []
        if self.repo:
            try:
                content = self.repo.get_contents(FILE_NAME)
                existing_data = json.loads(content.decoded_content.decode())
                if isinstance(existing_data, list):
                    self.existing_wallets = existing_data
                    print(f"Loaded {len(self.existing_wallets)} existing wallets")
                else:
                    print("Existing wallet data is not in the expected format, starting fresh")
            except Exception as e:
                print(f"No existing wallets file found or error loading: {str(e)}")
                print("A new wallets file will be created when the first wallet is found")

    def save_wallet(self, wallet_data):
        """Save wallet data to GitHub repository."""
        if not self.repo:
            print("GitHub repository not available. Wallet not saved to GitHub.")
            return

        try:
            try:
                # Try to get the existing file
                contents = self.repo.get_contents(FILE_NAME)
                # Get existing wallets
                existing_wallets = json.loads(contents.decoded_content.decode())
                if not isinstance(existing_wallets, list):
                    existing_wallets = []
                
                # Check if wallet already exists to avoid duplicates
                wallet_exists = any(
                    w['public_key'] == wallet_data['public_key'] 
                    for w in existing_wallets
                )
                
                if not wallet_exists:
                    # Add the new wallet
                    existing_wallets.append({
                        'public_key': wallet_data['public_key'],
                        'private_key': wallet_data['private_key']
                    })
                    
                    # Update the file with all wallets
                    self.repo.update_file(
                        FILE_NAME,
                        f"Add wallet: {wallet_data['public_key']}",
                        json.dumps(existing_wallets, indent=2),
                        contents.sha
                    )
                    print(f"✅ Saved wallet to GitHub: {wallet_data['public_key']}")
                else:
                    print(f"ℹ️ Wallet already exists in GitHub: {wallet_data['public_key']}")
                    
            except Exception as e:
                # File doesn't exist or other error, create new file with this wallet
                if 'Not Found' in str(e):
                    self.repo.create_file(
                        FILE_NAME,
                        "Initial commit: Add first wallet",
                        json.dumps([{
                            'public_key': wallet_data['public_key'],
                            'private_key': wallet_data['private_key']
                        }], indent=2)
                    )
                    print(f"✅ Created new file and saved wallet to GitHub: {wallet_data['public_key']}")
                else:
                    raise
                    
        except Exception as e:
            print(f"❌ Error saving to GitHub: {str(e)}")
            print("Make sure your GitHub token has write access to the repository.")

    @staticmethod
    def generate_wallet_batch(batch_size=1000):  # Increased default batch size
        """Generate a batch of Solana keypairs (highly optimized version)."""
        batch = []
        # Pre-allocate list for better performance
        batch = [None] * batch_size
        for i in range(batch_size):
            keypair = Keypair()
            pubkey = str(keypair.pubkey())
            batch[i] = ({
                'public_key': pubkey,
                'private_key': base58.b58encode(bytes(keypair)).decode('utf-8')
            }, pubkey.lower())
        return batch

    def process_wallet_batch(self, _):
        """Process a batch of wallets (optimized for performance)."""
        if shutdown_flag:
            return None
            
        # Process larger batches to reduce overhead
        batch = self.generate_wallet_batch(1000)  # Increased from 100 to 1000
        matches = []
        suffix_len = len(TARGET_SUFFIX)
        
        for wallet, pubkey_lower in batch:
            # Faster string comparison using slicing
            if pubkey_lower[-suffix_len:] == TARGET_SUFFIX:
                # Only check case if suffix matches
                if wallet['public_key'].endswith(TARGET_SUFFIX):
                    matches.append(('match', wallet))
                else:
                    matches.append(('reject', wallet['public_key']))
        
        # Update counter without lock if possible (faster)
        global wallet_counter
        if hasattr(wallet_counter, 'value'):  # For multiprocessing.Value
            wallet_counter.value += len(batch)
        else:  # Fallback for threading
            with counter_lock:
                wallet_counter += len(batch)
            
        return matches if matches else None

    def run(self):
        """Main wallet generation loop (optimized for performance)."""
        print(f"🚀 Starting to generate wallets ending with '{TARGET_SUFFIX}'...")
        
        # Use all available cores, but cap at 4 for free tier efficiency
        num_workers = min(cpu_count(), 4)  # Limit workers on free tier
        print(f"Using {num_workers} workers for parallel processing")
        
        # Initial GitHub connection test (moved outside the main loop)
        if not self.setup_github(test_only=True):
            print("⚠️ GitHub connection test failed. Wallets will be generated but not saved to GitHub.")
        else:
            print("✅ GitHub connection test successful. Will save wallets when found.")
            self.initial_github_check_done = True
        
        # Pre-allocate some memory
        self.start_time = time.time()
        self.last_print = self.start_time
        
        try:
            with Pool(processes=num_workers) as pool:
                batch_count = 0
                # Use imap_unordered with larger chunks for better performance
                for results in pool.imap_unordered(
                    self.process_wallet_batch, 
                    range(10**6),  # Large range to keep generating
                    chunksize=1  # Process one batch per worker at a time
                ):
                    if shutdown_flag:
                        break
                        
                    # Update counter (using batch size of 1000 now)
                    batch_count += 1
                    
                    # Print status less frequently to reduce overhead
                    current_time = time.time()
                    if current_time - self.last_print >= 5.0:  # Print every 5 seconds
                        self.print_status()
                    
                    # Process results if we found matches
                    if results:
                        for status, data in results:
                            if status == 'match':
                                wallet = data
                                public_key = wallet['public_key']
                                
                                # Add to found wallets (minimal processing)
                                with found_wallets_lock:
                                    found_wallets.append(wallet)
                                
                                # Print match (minimal formatting for speed)
                                print(f"\n🎉 Found match: {public_key}")
                            
                                # Save to GitHub in a separate thread to not block generation
                                if self.initial_github_check_done:
                                    try:
                                        # Save in background to avoid blocking
                                        threading.Thread(
                                            target=self.save_wallet,
                                            args=(wallet,),
                                            daemon=True
                                        ).start()
                                    except Exception as e:
                                        print(f"❌ Error queuing save: {str(e)}")
                    
                    if shutdown_flag:
                        print("\n👋 Shutting down...")
                        break

        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
        finally:
            self.print_status(force=True)
            print("\n✨ Wallet generation stopped!")
    
    def start(self):
        """Start the wallet generation in a separate thread."""
        if self.is_running:
            return False
        
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()
        return True
    
    def stop(self):
        """Stop the wallet generation."""
        global shutdown_flag
        if self.is_running:
            shutdown_flag = True
            self.worker_thread.join()
            shutdown_flag = False
            return True
        return False
    
    def get_status(self):
        """Get current status of wallet generation."""
        elapsed = time.time() - self.start_time
        rate = wallet_counter.value / elapsed if elapsed > 0 else 0
        
        return {
            'is_running': self.is_running,
            'wallets_checked': wallet_counter.value,
            'wallets_found': len(found_wallets),
            'elapsed_time': elapsed,
            'rate_per_second': rate
        }

# Initialize the wallet generator and start generation in a background thread
generator = WalletGenerator()

# Keep-alive control variables
keep_alive_active = False
keep_alive_thread = None
keep_alive_stop = threading.Event()

def keep_alive_worker():
    """Background thread function to ping the site."""
    global keep_alive_active
    import requests
    from datetime import datetime
    
    while not keep_alive_stop.is_set():
        try:
            # Default to the Render site if not specified
            site_url = os.getenv('RENDER_SITE_URL', 'https://newagers.onrender.com')
            
            # Ensure proper URL format
            if not site_url.startswith(('http://', 'https://')):
                site_url = f'https://{site_url}'
            site_url = site_url.rstrip('/')
            
            # Make the request with a user agent and timeout
            headers = {'User-Agent': 'BonkVanityKeepAlive/1.0'}
            start_time = time.time()
            response = requests.get(
                site_url, 
                timeout=10, 
                headers=headers,
                verify=True  # Verify SSL certificate
            )
            response_time = (time.time() - start_time) * 1000  # in milliseconds
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pinged {site_url} - Status: {response.status_code} ({response_time:.2f}ms)")
        except requests.exceptions.SSLError as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SSL Error: {str(e)}")
            # Wait a bit longer on SSL errors to avoid hammering
            keep_alive_stop.wait(300)  # 5 minutes
            continue
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error pinging site: {str(e)}")
        
        # Wait for 1 minute or until stop is requested
        keep_alive_stop.wait(60)
    
    keep_alive_active = False
    print("Keep-alive worker stopped")

def start_keep_alive():
    """Start the keep-alive background thread."""
    global keep_alive_active, keep_alive_thread, keep_alive_stop
    
    if not keep_alive_active:
        keep_alive_stop.clear()
        keep_alive_thread = threading.Thread(target=keep_alive_worker, daemon=True)
        keep_alive_thread.start()
        keep_alive_active = True
        return True
    return False

def stop_keep_alive():
    """Stop the keep-alive background thread."""
    global keep_alive_active, keep_alive_thread, keep_alive_stop
    
    if keep_alive_active:
        keep_alive_stop.set()
        keep_alive_thread = None
        keep_alive_active = False
        return True
    return False

@app.route('/')
def index():
    """Serve the main page with wallet generation stats and keep-alive controls."""
    with found_wallets_lock:
        # Get the current stats
        global wallet_counter, keep_alive_active
        with counter_lock:
            total_generated = wallet_counter
        total_matched = len(found_wallets)
        
        # Generate HTML response
        # Prepare the dynamic parts of the HTML
        active_class = ' active' if keep_alive_active else ''
        button_text = 'Stop Keep-Alive' if keep_alive_active else 'Start Keep-Alive'
        
        html = """<!DOCTYPE html>
        <html>
        <head>
            <title>Bonk Vanity Wallet Generator</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                .stats {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                .control-panel {{ background: #e9f7fe; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                button {{ 
                    background: #4CAF50; 
                    color: white; 
                    border: none; 
                    padding: 10px 20px; 
                    text-align: center; 
                    text-decoration: none; 
                    display: inline-block; 
                    font-size: 16px; 
                    margin: 4px 2px; 
                    cursor: pointer; 
                    border-radius: 4px;
                }}
                button:disabled {{ background: #cccccc; cursor: not-allowed; }}
                #status {{ font-weight: bold; }}
                .active {{ color: #4CAF50; }}
                .inactive {{ color: #f44336; }}
                .keep-alive-btn {{
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    z-index: 1000;
                    background: #4CAF50;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 5px;
                    cursor: pointer;
                }}
                .keep-alive-btn:disabled {{
                    background: #cccccc;
                    cursor: not-allowed;
                }}
                .keep-alive-btn.active {{
                    background: #f44336;
                }}
            </style>
        </head>
        <body>
            <h1>Bonk Vanity Wallet Generator</h1>
            
            <div class="stats">
                <h2>Wallet Generation Stats</h2>
                <p>Total Wallets Generated: {total_generated:,}</p>
                <p>Matching Wallets Found: {total_matched:,}</p>
                <p>Last updated: {current_time}</p>
            </div>
            
            <button id="keepAliveBtn" class="keep-alive-btn{active_class}" 
                    onclick="toggleKeepAlive()">
                {button_text}
            </button>
            
            <script>
                function toggleKeepAlive() {{
                    const btn = document.getElementById('keepAliveBtn');
                    const isStarting = btn.textContent.trim() === 'Start Keep-Alive';
                    btn.disabled = true;
                    
                    fetch(isStarting ? '/start-keepalive' : '/stop-keepalive', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                        }}
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            btn.textContent = data.active ? 'Stop Keep-Alive' : 'Start Keep-Alive';
                            if (data.active) {{
                                btn.classList.add('active');
                            }} else {{
                                btn.classList.remove('active');
                            }}
                            console.log(data.message);
                        }} else {{
                            console.error('Error:', data.message);
                        }}
                    }})
                    .catch(error => {{
                        console.error('Error:', error);
                    }})
                    .finally(() => {{
                        btn.disabled = false;
                    }});
                }}
            </script>
        </body>
        </html>""".format(
            total_generated=total_generated,
            total_matched=total_matched,
            current_time=time.strftime('%Y-%m-%d %H:%M:%S'),
            active_class=active_class,
            button_text=button_text
        )
        
        return Response(html, mimetype='text/html')

@app.route('/start-keepalive', methods=['POST'])
def start_keepalive():
    """Start the keep-alive ping."""
    global keep_alive_active, keep_alive_thread, keep_alive_stop
    
    if not keep_alive_active:
        keep_alive_stop = threading.Event()
        keep_alive_thread = threading.Thread(
            target=keep_alive_worker,
            daemon=True
        )
        keep_alive_thread.start()
        keep_alive_active = True
        print("✅ Keep-alive started")
    
    return jsonify({
        'success': True,
        'active': keep_alive_active,
        'message': 'Keep-alive started' if keep_alive_active else 'Keep-alive already running'
    })

@app.route('/stop-keepalive', methods=['POST'])
def stop_keepalive():
    """Stop the keep-alive ping."""
    global keep_alive_active, keep_alive_stop, keep_alive_thread
    
    if keep_alive_active:
        keep_alive_stop.set()
        if keep_alive_thread:
            keep_alive_thread.join(timeout=2.0)
        keep_alive_active = False
        print("🛑 Keep-alive stopped")
    
    return jsonify({
        'success': True,
        'active': keep_alive_active,
        'message': 'Keep-alive stopped' if not keep_alive_active else 'Failed to stop keep-alive'
    })

# Start wallet generation in a background thread
def start_wallet_generation():
    generator.run()

# Start the wallet generation when the script runs
if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("⚠️ Please set your GITHUB_TOKEN in the .env file!")
        exit(1)
    
    # Start wallet generation in a background thread
    wallet_thread = threading.Thread(target=start_wallet_generation, daemon=True)
    wallet_thread.start()
    
    # Register signal handler for clean shutdown
    def signal_handler(sig, frame):
        global shutdown_flag, keep_alive_active, keep_alive_thread
        print("\n👋 Shutting down gracefully...")
        shutdown_flag = True
        stop_keep_alive()
        if keep_alive_thread:
            keep_alive_thread.join(timeout=5)
        wallet_thread.join(timeout=5)
        print("Cleanup complete. Goodbye!")
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Keep-alive control endpoints are defined above the main block

    # Start the Flask app
    port = int(os.getenv('PORT', 5000))
    print(f"[Web] Server running on http://localhost:{port}")
    print("[Web] Access the web interface to control the keep-alive functionality")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

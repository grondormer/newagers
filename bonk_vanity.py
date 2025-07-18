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
from flask import Flask, Response

# Load environment variables from .env file
load_dotenv()

# Disable SSL warnings for GitHub API
import urllib3
urllib3.disable_warnings()

# Global flag for clean shutdown
shutdown_flag = False
app = Flask(__name__)

# Store found wallets
found_wallets = []
found_wallets_lock = threading.Lock()

# Global counter for wallet generation
wallet_counter = 0
counter_lock = threading.Lock()

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

        # Add the new wallet to our list
        self.existing_wallets.append(wallet_data)
        
        # Prepare wallet data for GitHub (only public and private keys)
        wallet_data_clean = [
            {'public_key': w['public_key'], 'private_key': w['private_key']}
            for w in self.existing_wallets
        ]
        
        try:
            # Try to get the existing file
            try:
                contents = self.repo.get_contents(FILE_NAME)
                # File exists, update it
                self.repo.update_file(
                    FILE_NAME,
                    f"Add wallet {wallet_data['public_key'][-10:]}",
                    json.dumps(wallet_data_clean, indent=2),
                    contents.sha
                )
            except:
                # File doesn't exist, create it
                self.repo.create_file(
                    FILE_NAME,
                    "Initial commit: Add first wallet",
                    json.dumps(wallet_data_clean, indent=2)
                )
            print(f"✅ Saved wallet to GitHub: {wallet_data['public_key']}")
        except Exception as e:
            print(f"❌ Error saving to GitHub: {str(e)}")
            print("Make sure your GitHub token has write access to the repository.")

    @staticmethod
    def generate_wallet_batch(batch_size=100):
        """Generate a batch of Solana keypairs (optimized version)."""
        batch = []
        for _ in range(batch_size):
            keypair = Keypair()
            pubkey = str(keypair.pubkey())
            batch.append(({
                'public_key': pubkey,
                'private_key': base58.b58encode(bytes(keypair)).decode('utf-8')
            }, pubkey.lower()))
        return batch

    def process_wallet_batch(self, _):
        """Process a batch of wallets (for multiprocessing)."""
        if shutdown_flag:
            return None
            
        batch = self.generate_wallet_batch(100)  # Process 100 wallets at once
        matches = []
        
        for wallet, pubkey_lower in batch:
            if pubkey_lower.endswith(TARGET_SUFFIX):
                if wallet['public_key'].endswith(TARGET_SUFFIX):  # Case-sensitive check
                    matches.append(('match', wallet))
                else:
                    matches.append(('reject', wallet['public_key']))
        
        # Update the global counter in a thread-safe way
        global wallet_counter
        with counter_lock:
            wallet_counter += len(batch)
            
        return matches if matches else None

    def run(self):
        """Main wallet generation loop."""
        print(f"🚀 Starting to generate wallets ending with '{TARGET_SUFFIX}'...")
        print(f"Using {cpu_count()} CPU cores for parallel processing")
        
        # Initial GitHub connection test
        if not self.setup_github(test_only=True):
            print("⚠️ GitHub connection test failed. Wallets will be generated but not saved to GitHub.")
        else:
            print("✅ GitHub connection test successful. Will save wallets when found.")
            self.initial_github_check_done = True
        
        try:
            with Pool(processes=cpu_count()) as pool:
                batch_count = 0
                
                for results in pool.imap_unordered(
                    self.process_wallet_batch, 
                    range(10**6),
                    chunksize=10
                ):
                    if shutdown_flag:
                        break
                        
                    # Update counter with the batch size (100 wallets per batch)
                    batch_count += 1
                    global wallet_counter
                    with counter_lock:
                        wallet_counter = batch_count * 100
                    
                    # Print status periodically
                    if time.time() - self.last_print >= self.print_interval:
                        self.print_status()
                    
                    # Process results if we found matches
                    if results:
                        for status, data in results:
                            if status == 'match':
                                wallet = data
                                public_key = wallet['public_key']
                                
                                # Add to wallets set and found_wallets list
                                self.wallets.add(public_key)
                                with found_wallets_lock:
                                    found_wallets.append(wallet)
                                
                                # Print match
                                print("\n\n🎉 Found matching wallet!")
                                print(f"🔑 Public Key: {public_key}")
                                print(f"🔒 Private Key: {wallet['private_key']}")
                            
                                # Save to GitHub if configured
                                if self.initial_github_check_done:
                                    try:
                                        self.save_wallet(wallet)
                                    except Exception as e:
                                        print(f"❌ Error saving wallet to GitHub: {str(e)}")
                    
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

@app.route('/')
def index():
    """Serve a simple page showing wallet generation stats."""
    with found_wallets_lock:
        # Get the current stats
        global wallet_counter
        with counter_lock:
            total_generated = wallet_counter
        total_matched = len(found_wallets)
        
        # Create a simple text response with stats
        response = (
            f"Bonk Vanity Wallet Generator\n"
            f"===========================\n\n"
            f"Total Wallets Generated: {total_generated:,}\n"
            f"Matching Wallets Found: {total_matched:,}\n"
            f"\nLast updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        return Response(response, mimetype='text/plain')

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
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the Flask app
    port = int(os.getenv('PORT', 5000))
    print(f"[Web] Server running on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

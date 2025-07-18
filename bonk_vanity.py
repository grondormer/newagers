import base58
import json
import os
import time
import multiprocessing
from multiprocessing import Pool, cpu_count
from solders.keypair import Keypair
from github import Github, Auth
import signal
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Disable SSL warnings for GitHub API
import urllib3
urllib3.disable_warnings()

# Global flag for clean shutdown
shutdown_flag = False

def signal_handler(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    print("\n👋 Shutting down gracefully...")

# Register signal handler for clean shutdown
signal.signal(signal.SIGINT, signal_handler)

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
        self.existing_wallets = []  # Initialize the list to store existing wallets
        self.last_save = 0
        self.save_interval = 60  # Save to GitHub every 60 seconds
        self.initial_github_check_done = False
        self.counter = 0
        self.start_time = time.time()
        self.last_print = time.time()
        self.print_interval = 30.0  # Print status every 30 seconds
        self.last_rate_check = time.time()
        self.last_counter = 0

    def print_status(self, force=False):
        current_time = time.time()
        time_since_last_print = current_time - self.last_print
        
        # Always calculate rate based on last 30 seconds window
        if current_time - self.last_rate_check >= 30.0:
            elapsed = current_time - self.last_rate_check
            wallets_checked = self.counter - self.last_counter
            rate = wallets_checked / elapsed if elapsed > 0 else 0
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            print(f"[{timestamp}] 🔍 Total: {self.counter:,} wallets | "
                  f"Rate: {rate:,.0f} w/s (last 30s) | "
                  f"Found: {len(self.wallets):,}", flush=True)
                  
            self.last_rate_check = current_time
            self.last_counter = self.counter
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
        
        self.counter += len(batch)
        return matches if matches else None

    def run(self):
        """Main loop to generate vanity wallets (optimized version)."""
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
                # Use a larger chunksize for better performance
                # Initialize batch counter
                batch_count = 0
                
                for results in pool.imap_unordered(
                    self.process_wallet_batch, 
                    range(10**6),  # Adjust based on your needs
                    chunksize=10   # Process in chunks to reduce overhead
                ):
                    if shutdown_flag:
                        break
                        
                    # Update counter with the batch size (100 wallets per batch)
                    batch_count += 1
                    self.counter = batch_count * 100  # 100 wallets per batch
                        
                    # Print status periodically
                    self.print_status()
                    
                    # Process results if we found matches
                    if results:
                        for status, data in results:
                            if status == 'match':
                                wallet = data
                                public_key = wallet['public_key']
                                
                                # Add to wallets set
                                self.wallets.add(public_key)
                                
                                # Print match
                                print("\n\n🎉 Found matching wallet!")
                                print(f"🔑 Public Key: {public_key}")
                                print(f"🔒 Private Key: {wallet['private_key']}")
                            
                                # Initialize GitHub connection if not already done
                                if not self.initial_github_check_done:
                                    if self.setup_github():
                                        self.initial_github_check_done = True
                                    else:
                                        print("⚠️ Could not connect to GitHub. Wallet not saved!")
                                        continue
                                    
                                # Save to GitHub
                                try:
                                    self.save_wallet(wallet)
                                except Exception as e:
                                    print(f"❌ Error saving wallet to GitHub: {str(e)}")
                            
                            elif status == 'reject':
                                public_key = data
                                # Optional: Uncomment to see rejected wallets
                                # last_four = public_key[-4:]
                                # colored = f"{public_key[:-4]}\033[92m{last_four}\033[0m"
                                # print(f"\n❌ {colored} - Rejected (wrong case)")
                                continue  # Removed the typo 'continuet'
                    
                    # Check for shutdown flag
                    if shutdown_flag:
                        print("\n👋 Shutting down...")
                        break

        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
        finally:
            # Final status update
            self.print_status(force=True)
            print("\n✨ Done!")
            
            # Print summary
            elapsed = time.time() - self.start_time
            print(f"Total wallets checked: {self.counter:,}")
            print(f"Total matches found: {len(self.wallets):,}")
            if elapsed > 0:
                rate = self.counter / elapsed
                print(f"Average rate: {rate:,.2f} wallets/second")
            print(f"Elapsed time: {elapsed:.1f} seconds")

if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("⚠️ Please set your GITHUB_TOKEN in the .env file!")
        exit(1)
        
    generator = WalletGenerator()
    generator.run()

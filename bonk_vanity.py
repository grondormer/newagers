import base58
import json
import time
import multiprocessing
from multiprocessing import Pool, cpu_count
from solders.keypair import Keypair
from github import Github, Auth
import signal

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
GITHUB_TOKEN = "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"  # GitHub token for repository access
REPO_NAME = "grondormer/vanbonkwal"  # Your repository
FILE_NAME = "wallets.json"  # File to store wallets in the repo

class WalletGenerator:
    def __init__(self):
        self.github = None
        self.repo = None
        self.wallets = set()
        self.last_save = 0
        self.save_interval = 60  # Save to GitHub every 60 seconds
        self.setup_github()
        self.load_existing_wallets()
        self.counter = 0
        self.start_time = time.time()
        self.last_print = 0
        self.print_interval = 1.0  # Print status every 1 second

    def print_status(self, force=False):
        current_time = time.time()
        if force or (current_time - self.last_print) >= self.print_interval:
            elapsed = current_time - self.start_time
            rate = self.counter / elapsed if elapsed > 0 else 0
            print(f"\r🔍 Checking: {self.counter:,} wallets | "
                  f"Rate: {rate:,.0f} w/s | "
                  f"Found: {len(self.wallets):,}", end='', flush=True)
            self.last_print = current_time

    def setup_github(self):
        """Initialize GitHub connection and get the repository."""
        if not GITHUB_TOKEN or GITHUB_TOKEN == "your_github_token_here":
            print("❌ GitHub token not set or using default value.")
            return
            
        if len(GITHUB_TOKEN) < 40:  # GitHub tokens are usually 40+ characters
            print("❌ Invalid GitHub token: Token appears to be too short")
            return
            
        try:
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
            
            # Test repository access
            try:
                contents = self.repo.get_contents(FILE_NAME)
                print(f"📁 Found existing {FILE_NAME} in repository")
            except:
                print(f"ℹ️ {FILE_NAME} not found in repository. It will be created when the first wallet is found.")
                
        except Exception as e:
            print(f"❌ Error connecting to GitHub: {str(e)}")
            print("Please verify:")
            print("1. Your GitHub token is correct and has 'repo' scope")
            print(f"2. The repository '{REPO_NAME}' exists and is accessible")
            print("3. The token has the correct permissions")
            self.repo = None

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
    def generate_wallet():
        """Generate a new Solana keypair (optimized version)."""
        keypair = Keypair()
        pubkey = str(keypair.pubkey())
        return {
            'public_key': pubkey,
            'private_key': base58.b58encode(bytes(keypair)).decode('utf-8')
        }, pubkey[-4:]  # Return the last 4 chars for faster checking

    def process_wallet(self, _):
        """Process a single wallet generation (for multiprocessing)."""
        if shutdown_flag:
            return None
            
        wallet, last_four = self.generate_wallet()
        public_key = wallet['public_key']
        
        if last_four.lower() == TARGET_SUFFIX:
            if last_four == TARGET_SUFFIX:
                # Found a match
                return ('match', wallet)
            else:
                # Rejected due to case
                return ('reject', public_key)
        return None

    def run(self):
        """Main loop to generate vanity wallets (optimized version)."""
        print(f"🚀 Starting to generate wallets ending with '{TARGET_SUFFIX}'...")
        print(f"Using {cpu_count()} CPU cores for parallel processing")
        
        try:
            with Pool(processes=cpu_count()) as pool:
                for result in pool.imap_unordered(self.process_wallet, range(10**9)):
                    self.counter += 1
                    
                    # Print status periodically
                    self.print_status()
                    
                    # Process result if we found something
                    if result:
                        status, data = result
                        if status == 'match':
                            wallet = data
                            public_key = wallet['public_key']
                            
                            # Skip if we already have this wallet
                            if public_key in self.wallets:
                                continue
                                
                            self.wallets.add(public_key)
                            
                            # Print match
                            print(f"\n\n🎉 Found matching wallet!")
                            print(f"🔑 Public Key: {public_key}")
                            print(f"🔒 Private Key: {wallet['private_key']}")
                            print("Saving to GitHub...")
                            
                            # Save to GitHub
                            self.save_wallet(wallet)
                            
                        elif status == 'reject':
                            public_key = data
                            # Optional: Uncomment to see rejected wallets
                            # last_four = public_key[-4:]
                            # colored = f"{public_key[:-4]}\033[92m{last_four}\033[0m"
                            # print(f"\n❌ {colored} - Rejected (wrong case)")
                    
                    # Check for shutdown flag
                    if shutdown_flag:
                        print("\n👋 Shutting down...")
                        break
                        
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
        finally:
            # Final status
            elapsed = time.time() - self.start_time
            print(f"\n\n🏁 Finished!")
            print(f"Total wallets checked: {self.counter:,}")
            print(f"Total matches found: {len(self.wallets):,}")
            print(f"Average speed: {self.counter/max(1, elapsed):,.0f} wallets/second")
            print(f"Elapsed time: {elapsed:.1f} seconds")

if __name__ == "__main__":
    if not GITHUB_TOKEN or GITHUB_TOKEN == "your_github_token_here":
        print("⚠️ Please set your GitHub token in the script first!")
        exit(1)
        
    generator = WalletGenerator()
    generator.run()

# Bonk Vanity Wallet Generator

This script generates Solana vanity wallets that end with "bonk" and saves them to a private GitHub repository.

## Prerequisites

1. Python 3.8+
2. GitHub account with a private repository
3. GitHub Personal Access Token with `repo` scope

## Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your GitHub token:
   ```bash
   cp .env.example .env
   ```
   Edit the `.env` file and add your GitHub Personal Access Token.

4. Create a private repository on GitHub where the wallets will be stored.

## Usage

Run the script:
```bash
python bonk_vanity.py
```

The script will:
1. Generate Solana keypairs
2. Check if the public key ends with "bonk"
3. Save matching wallets to your GitHub repository
4. Continue running until stopped (Ctrl+C)

## Running on Render

1. Push this repository to GitHub
2. Create a new Web Service on Render
3. Set the following environment variables in Render:
   - `GITHUB_TOKEN`: Your GitHub Personal Access Token
   - `REPO_NAME`: (Optional) Your repository name (defaults to 'bonk-wallets')
4. Set the build command: `pip install -r requirements.txt`
5. Set the start command: `python bonk_vanity.py`
6. Deploy!

## Security Notes

- Keep your `.env` file and GitHub token secure
- Never commit your `.env` file to version control
- The private keys are only stored in your private GitHub repository
- Consider using a dedicated GitHub account for this purpose

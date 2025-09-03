import os
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_env():
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info("Loading .env file...")
    load_dotenv(override=True)
    
    api_key = os.getenv("AIRNOW_API_KEY")
    logger.info(f"API key present: {'Yes' if api_key else 'No'}")
    if api_key:
        logger.info(f"First 8 chars of API key: {api_key[:8]}...")

    # List all environment variables (careful with sensitive data!)
    logger.info("All environment variables:")
    for key in os.environ:
        if 'KEY' in key or 'SECRET' in key:
            logger.info(f"{key}: {'*' * 8}")
        else:
            logger.info(f"{key}: {os.environ[key]}")

if __name__ == "__main__":
    test_env()
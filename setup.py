import boto3
import botocore.exceptions
import subprocess
import platform
import os
import time
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    filename="aws_connection_setup.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

def log(message):
    """Log messages with a timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    logger.info(message)

def install_aws_cli():
    """Install the AWS CLI if not already installed."""
    try:
        subprocess.run(["aws", "--version"], check=True)
        log("AWS CLI is already installed.")
    except FileNotFoundError:
        log("AWS CLI is not installed. Installing...")
        system = platform.system().lower()
        if "windows" in system:
            installer_url = "https://awscli.amazonaws.com/AWSCLIV2.msi"
            installer_path = "AWSCLIV2.msi"
            subprocess.run(["msiexec", "/i", installer_path, "/quiet", "/norestart"], check=True)
        elif "linux" in system:
            subprocess.run(["curl", "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip", "-o", "awscliv2.zip"], check=True)
            subprocess.run(["unzip", "awscliv2.zip"], check=True)
            subprocess.run(["sudo", "./aws/install"], check=True)
        elif "darwin" in system:
            installer_url = "https://awscli.amazonaws.com/AWSCLIV2.pkg"
            subprocess.run(["curl", "-o", "AWSCLIV2.pkg", installer_url], check=True)
            subprocess.run(["sudo", "installer", "-pkg", "AWSCLIV2.pkg", "-target", "/"], check=True)
        else:
            raise OSError("Unsupported Operating System")
        log("AWS CLI installed successfully.")

def validate_aws_credentials():
    """Validate AWS credentials."""
    try:
        sts = boto3.client('sts')
        response = sts.get_caller_identity()
        log(f"Validated AWS credentials for account: {response['Account']}")
    except botocore.exceptions.NoCredentialsError:
        log("AWS credentials not found. Please configure them.")
        raise
    except botocore.exceptions.PartialCredentialsError:
        log("Incomplete AWS credentials. Please verify configuration.")
        raise

def validate_cloudwatch_connection(region):
    """Validate connection to CloudWatch."""
    cloudwatch = boto3.client('cloudwatch', region_name=region)
    try:
        cloudwatch.list_metrics()
        log(f"Successfully connected to CloudWatch in region {region}.")
    except botocore.exceptions.EndpointConnectionError:
        log("Failed to connect to CloudWatch endpoint.")
        raise
    except Exception as e:
        log(f"Unexpected error while connecting to CloudWatch: {e}")
        raise

def setup_aws_connection(region):
    """Set up and validate all AWS connection requirements."""
    install_aws_cli()
    validate_aws_credentials()
    validate_cloudwatch_connection(region)
    log("AWS connection setup complete.")

if __name__ == "__main__":
    REGION = "us-east-1"  # Specify your AWS region
    setup_aws_connection(REGION)

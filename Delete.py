import time
import os
import yaml
import boto3
import botocore.exceptions
import paramiko
import subprocess
import platform
import urllib.request
import zipfile
import random
import string
import logging
import datetime

def log(message):
    """Print a message with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    logger.info(message)  # Log to the file using the logger

logging.basicConfig(
    filename="process.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

def install_dependencies():
    """Install Python dependencies if not already installed."""
    dependencies = ["boto3", "PyYAML", "paramiko"]

    for package in dependencies:
        try:
            # Check if the package is already installed
            result = subprocess.run(["pip", "show", package], capture_output=True, text=True, check=True)
            if result.stdout:
                log(f"{package} is already installed.")
            else:
                raise subprocess.CalledProcessError(1, "pip show")
        except subprocess.CalledProcessError:
            log(f"Installing {package}...")
            subprocess.run(["pip", "install", package], check=True)
            log(f"{package} installed successfully.")

def install_aws_cli():
    """Download and install AWS CLI if not already installed."""
    try:
        # Check if AWS CLI is already installed
        result = subprocess.run(["aws", "--version"], capture_output=True, text=True, check=True)
        log(f"AWS CLI is already installed: {result.stdout.strip()}")
        return  # Skip installation if already installed
    except FileNotFoundError:
        log("AWS CLI is not installed. Proceeding with installation.")

    system = platform.system().lower()

    if "windows" in system:
        # Download AWS CLI installer for Windows
        installer_url = "https://awscli.amazonaws.com/AWSCLIV2.msi"
        installer_path = "AWSCLIV2.msi"
        urllib.request.urlretrieve(installer_url, installer_path)
        log("AWS CLI installer downloaded.")

        # Run the installer with administrative privileges
        subprocess.run(["powershell", "Start-Process", "msiexec.exe", "-ArgumentList", f"/i {installer_path} /quiet /norestart", "-Verb", "runAs"], check=True)
        os.remove(installer_path)
        log("AWS CLI installed successfully.")

    elif "linux" in system or "darwin" in system:
        # Download AWS CLI installer for Linux/Mac
        installer_url = "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" if "linux" in system else "https://awscli.amazonaws.com/AWSCLIV2.pkg"
        installer_path = "AWSCLIV2.zip" if "linux" in system else "AWSCLIV2.pkg"
        urllib.request.urlretrieve(installer_url, installer_path)
        log("AWS CLI installer downloaded.")

        if "linux" in system:
            # Extract and install for Linux with administrative privileges
            with zipfile.ZipFile(installer_path, 'r') as zip_ref:
                zip_ref.extractall("awscli-install")
            subprocess.run(["sudo", "./awscli-install/aws/install"], check=True)
            os.remove(installer_path)
            log("AWS CLI installed successfully.")
        else:
            # Install for Mac with administrative privileges
            subprocess.run(["sudo", "installer", "-pkg", installer_path, "-target", "/"], check=True)
            os.remove(installer_path)
            log("AWS CLI installed successfully.")
    else:
        raise OSError("Unsupported Operating System")

    # Verify installation
    subprocess.run(["aws", "--version"], check=True)

def validate_config(config):
    for env in config['environments']:
        for node in env['nodes']:
            if 'instance_type' not in node or 'ami_id' not in node:
                raise ValueError(f"Missing 'instance_type' or 'ami_id' for {node['type']} in {env['name']}")

def delete_customer_resources(customer_code, region, config):
    """Delete all AWS resources associated with a specific customer tag."""
    ec2 = boto3.client('ec2', region_name=region)
    tgw = boto3.client('ec2', region_name=region)  # Transit Gateway client
    iam = boto3.client('iam')
    budgets = boto3.client('budgets', region_name=region)

    try:
        log(f"Deleting resources for customer: {customer_code}")

        # Delete Key Pair
        key_pair_name = f"{customer_code}-key"
        try:
            ec2.delete_key_pair(KeyName=key_pair_name)
            log(f"Deleted Key Pair: {key_pair_name}")
        except botocore.exceptions.ClientError as e:
            log(f"Failed to delete Key Pair {key_pair_name}: {e}")

        # Detach and delete Transit Gateway Attachments
        tgw_attachments = tgw.describe_transit_gateway_attachments(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for attachment in tgw_attachments['TransitGatewayAttachments']:
            try:
                tgw.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=attachment['TransitGatewayAttachmentId'])
                log(f"Deleted Transit Gateway Attachment: {attachment['TransitGatewayAttachmentId']}")
            except Exception as e:
                log(f"Failed to delete Transit Gateway Attachment {attachment['TransitGatewayAttachmentId']}: {e}")

        # Delete Budgets
        try:
            budgets.delete_budget(
                AccountId=config['account_id'],
                BudgetName=f"{customer_code}-budget"
            )
            log(f"Deleted Budget: {customer_code}-budget")
        except botocore.exceptions.ClientError as e:
            log(f"Failed to delete budget {customer_code}-budget: {e}")

        # Detach and delete IAM Policies
        environment_codes = config.get('environment_codes', ['01', '04'])
        iam_user_types = ['service', 'promotion', 'restricted']
        iam_users = [
            f"{customer_code}-{env_code}-{user_type}"
            for env_code in environment_codes
            for user_type in iam_user_types
        ]
        iam_users.append(f"{customer_code}-admin")

        for user in iam_users:
            try:
                policies = iam.list_attached_user_policies(UserName=user)['AttachedPolicies']
                for policy in policies:
                    iam.detach_user_policy(UserName=user, PolicyArn=policy['PolicyArn'])
                log(f"Detached policies for IAM user: {user}")

                # Delete Login Profile
                try:
                    iam.delete_login_profile(UserName=user)
                    log(f"Deleted login profile for IAM user: {user}")
                except botocore.exceptions.ClientError as e:
                    log(f"Failed to delete login profile for IAM user {user}: {e}")

                # Delete IAM User
                iam.delete_user(UserName=user)
                log(f"Deleted IAM user: {user}")
            except botocore.exceptions.ClientError as e:
                log(f"Failed to delete IAM user {user}: {e}")

        # Delete Security Groups
        try:
            security_groups = ec2.describe_security_groups(Filters=[
                {'Name': 'tag:Customer', 'Values': [customer_code]}
            ])['SecurityGroups']
            for sg in security_groups:
                ec2.delete_security_group(GroupId=sg['GroupId'])
                log(f"Deleted Security Group: {sg['GroupId']}")
        except botocore.exceptions.ClientError as e:
            log(f"Failed to delete Security Group: {e}")

        # Delete Subnets
        try:
            subnets = ec2.describe_subnets(Filters=[
                {'Name': 'tag:Customer', 'Values': [customer_code]}
            ])['Subnets']
            for subnet in subnets:
                ec2.delete_subnet(SubnetId=subnet['SubnetId'])
                log(f"Deleted Subnet: {subnet['SubnetId']}")
        except botocore.exceptions.ClientError as e:
            log(f"Failed to delete Subnet: {e}")

        # Delete VPC
        try:
            vpcs = ec2.describe_vpcs(Filters=[
                {'Name': 'tag:Customer', 'Values': [customer_code]}
            ])['Vpcs']
            for vpc in vpcs:
                ec2.delete_vpc(VpcId=vpc['VpcId'])
                log(f"Deleted VPC: {vpc['VpcId']}")
        except botocore.exceptions.ClientError as e:
            log(f"Failed to delete VPC: {e}")

        log(f"All resources for customer {customer_code} have been deleted.")

    except Exception as e:
        log(f"Error deleting resources for customer {customer_code}: {e}")
        raise

def load_config(config_file):
    """Load configuration from YAML file."""
    with open(config_file, 'r') as file:
        return yaml.safe_load(file)

def main():
    config = load_config('config.yaml')
    customer_code = config['customer_code']
    region = config['region']
    install_dependencies()
    validate_config(config)
    if config.get('delete_resources', True):
        delete_customer_resources(customer_code, region, config)
   
if __name__ == "__main__":
    main()

"""
202412100933 Matt Baker
Version 0.0.1
Welcome to the Qlik Sense On Premise Rapid Onboarding.  The goal of this script is to standup and deploy all needed AWS resources to host a Qlik Sense server solution on AWS in as short amount of time as possible.

This script is currently in test mode.  It uses small ec2 nodes not normally designed to handle full Qlik Sense BI server specs.  Similarly, it uses stand in AMIs to save on space, not actual full Windows server elements.

Todo list:
1.) Setup Cloud monitoring (needs an SSL cert and connections made up)
2.) Setup the AMIs for a Qlik Sense core node, a Qlik Sense support node, an NPrinting node, and a Platform Manager node.
3.) Port out the modules into support files.
"""
import os
import time
import yaml
import boto3
import botocore.exceptions
import paramiko
import subprocess
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

        # Delete EC2 Instances
        instances = ec2.describe_instances(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        instance_ids = [i['InstanceId'] for r in instances['Reservations'] for i in r['Instances']]
        if instance_ids:
            ec2.terminate_instances(InstanceIds=instance_ids)
            log(f"Terminating instances: {instance_ids}")
            try:
                waiter = ec2.get_waiter('instance_terminated')
                waiter.wait(InstanceIds=instance_ids, WaiterConfig={'Delay': 15, 'MaxAttempts': 20})
                log("Instances terminated successfully.")
            except botocore.exceptions.WaiterError as e:
                log(f"Waiter for instance termination failed: {e}. Proceeding with deletion.")

        # Delete IAM Users
        iam_users = [f"{customer_code}-admin"]
        for env in ['01', '04']:
            for account_type in ['service', 'promotion', 'restricted']:
                iam_users.append(f"{customer_code}-{env}-{account_type}")
        for user in iam_users:
            try:
                attached_policies = iam.list_attached_user_policies(UserName=user).get('AttachedPolicies', [])
                for policy in attached_policies:
                    iam.detach_user_policy(UserName=user, PolicyArn=policy['PolicyArn'])

                inline_policies = iam.list_user_policies(UserName=user).get('PolicyNames', [])
                for policy in inline_policies:
                    iam.delete_user_policy(UserName=user, PolicyName=policy)

                iam.delete_login_profile(UserName=user)
                iam.delete_user(UserName=user)
                log(f"Deleted IAM user: {user}")
            except Exception as e:
                log(f"Failed to delete IAM user {user}: {e}")

        # Delete Budgets
        try:
            response = budgets.describe_budgets(AccountId=config['account_id'])
            for budget in response.get('Budgets', []):
                if budget['BudgetName'].startswith(customer_code):
                    budgets.delete_budget(AccountId=config['account_id'], BudgetName=budget['BudgetName'])
                    log(f"Deleted Budget: {budget['BudgetName']}")
        except Exception as e:
            log(f"Failed to delete budgets: {e}")

        # Delete Network Interfaces
        network_interfaces = ec2.describe_network_interfaces(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for ni in network_interfaces['NetworkInterfaces']:
            try:
                ec2.delete_network_interface(NetworkInterfaceId=ni['NetworkInterfaceId'])
                log(f"Deleted Network Interface: {ni['NetworkInterfaceId']}")
            except Exception as e:
                log(f"Failed to delete Network Interface {ni['NetworkInterfaceId']}: {e}")

        # Delete NAT Gateways
        nat_gateways = ec2.describe_nat_gateways(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for nat in nat_gateways['NatGateways']:
            try:
                ec2.delete_nat_gateway(NatGatewayId=nat['NatGatewayId'])
                log(f"Deleted NAT Gateway: {nat['NatGatewayId']}")
            except Exception as e:
                log(f"Failed to delete NAT Gateway {nat['NatGatewayId']}: {e}")

        # Delete Security Groups
        security_groups = ec2.describe_security_groups(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for sg in security_groups['SecurityGroups']:
            try:
                ec2.delete_security_group(GroupId=sg['GroupId'])
                log(f"Deleted Security Group: {sg['GroupId']}")
            except Exception as e:
                log(f"Failed to delete Security Group {sg['GroupId']}: {e}")

        # Delete Route Tables
        route_tables = ec2.describe_route_tables(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for rt in route_tables['RouteTables']:
            try:
                ec2.delete_route_table(RouteTableId=rt['RouteTableId'])
                log(f"Deleted Route Table: {rt['RouteTableId']}")
            except Exception as e:
                log(f"Failed to delete Route Table {rt['RouteTableId']}: {e}")

        # Delete Subnets
        subnets = ec2.describe_subnets(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for subnet in subnets['Subnets']:
            try:
                ec2.delete_subnet(SubnetId=subnet['SubnetId'])
                log(f"Deleted Subnet: {subnet['SubnetId']}")
            except Exception as e:
                log(f"Failed to delete Subnet {subnet['SubnetId']}: {e}")

        # Delete Internet Gateways
        igws = ec2.describe_internet_gateways(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for igw in igws['InternetGateways']:
            try:
                ec2.detach_internet_gateway(InternetGatewayId=igw['InternetGatewayId'], VpcId=igw['Attachments'][0]['VpcId'])
                ec2.delete_internet_gateway(InternetGatewayId=igw['InternetGatewayId'])
                log(f"Deleted Internet Gateway: {igw['InternetGatewayId']}")
            except Exception as e:
                log(f"Failed to delete Internet Gateway {igw['InternetGatewayId']}: {e}")

        # Delete Transit Gateways
        transit_gateways = tgw.describe_transit_gateways(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for tg in transit_gateways['TransitGateways']:
            try:
                tgw.delete_transit_gateway(TransitGatewayId=tg['TransitGatewayId'])
                log(f"Deleted Transit Gateway: {tg['TransitGatewayId']}")
            except Exception as e:
                log(f"Failed to delete Transit Gateway {tg['TransitGatewayId']}: {e}")

        # Delete VPCs
        vpcs = ec2.describe_vpcs(Filters=[
            {'Name': 'tag:Customer', 'Values': [customer_code]}
        ])
        for vpc in vpcs['Vpcs']:
            try:
                ec2.delete_vpc(VpcId=vpc['VpcId'])
                log(f"Deleted VPC: {vpc['VpcId']}")
            except Exception as e:
                log(f"Failed to delete VPC {vpc['VpcId']}: {e}")

        log(f"All resources for customer {customer_code} have been deleted.")

    except Exception as e:
        log(f"Error deleting resources for customer {customer_code}: {e}")
        raise

def create_ec2_instances(config, vpc_resources):
    """Create EC2 instances for the customer's environments."""
    ec2 = boto3.client('ec2', region_name=config['region'])

    # Ensure the key pair exists or create it
    key_name = f"{config['customer_code']}-key"
    try:
        existing_keys = ec2.describe_key_pairs(KeyNames=[key_name])
        log(f"Key Pair {key_name} already exists.")
    except botocore.exceptions.ClientError as e:
        if 'InvalidKeyPair.NotFound' in str(e):
            log(f"Key Pair {key_name} not found. Creating new key pair.")
            key_pair = ec2.create_key_pair(KeyName=key_name)
            with open(f"{key_name}.pem", "w") as key_file:
                key_file.write(key_pair['KeyMaterial'])
            log(f"Created Key Pair: {key_name}")
        else:
            log(f"Unexpected error while checking key pairs: {e}")
            raise

    for env in config['environments']:
        env_name = env['name']
        subnet_id = vpc_resources['subnets'][env_name]
        for node in env['nodes']:
            try:
                # Validate instance parameters
                if 'instance_type' not in node or 'ami_id' not in node:
                    log(f"Error: Missing 'instance_type' or 'ami_id' for {node['type']} in {env_name}")
                    continue

                instance_type = node['instance_type']
                ami_id = node['ami_id']
                tags = [
                    {'Key': 'Customer', 'Value': config['customer_code']},
                    {'Key': 'Environment', 'Value': env_name},
                    {'Key': 'Node', 'Value': node['type']}
                ]

                instance = ec2.run_instances(
                    ImageId=ami_id,
                    InstanceType=instance_type,
                    KeyName=key_name,
                    SubnetId=subnet_id,
                    MinCount=1,
                    MaxCount=1,
                    TagSpecifications=[
                        {
                            'ResourceType': 'instance',
                            'Tags': tags
                        }
                    ]
                )
                instance_id = instance['Instances'][0]['InstanceId']
                log(f"Launched EC2 instance {instance_id} for {node['type']} in {env_name}")
            except Exception as e:
                log(f"Error launching EC2 instance for {node['type']} in {env_name}: {e}")

def create_internet_gateway_with_retry(ec2, max_retries=3):
    for attempt in range(max_retries):
        try:
            igw = ec2.create_internet_gateway()
            logger.info(f"Successfully created Internet Gateway: {igw}")
            return igw
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    raise Exception("Failed to create Internet Gateway after multiple attempts.")

def create_vpc_with_tgw(config):
    """Create a dedicated VPC and attach it to a specified Transit Gateway with Elastic Network Interfaces."""
    ec2 = boto3.client('ec2', region_name=config['region'])
    tgw = boto3.client('ec2', region_name=config['region'])  # Transit Gateway client

    try:
        # Use the provided Transit Gateway ID from the config
        transit_gateway_id = config['transit_gateway_id']
        log(f"Using Transit Gateway: {transit_gateway_id}")

        # Validate Transit Gateway
        try:
            tgw_response = tgw.describe_transit_gateways(TransitGatewayIds=[transit_gateway_id])
            if not tgw_response['TransitGateways']:
                log(f"Transit Gateway {transit_gateway_id} does not exist. Skipping attachment.")
                return None  # Skip further TGW-related operations
            log(f"Validated Transit Gateway: {transit_gateway_id}")
        except botocore.exceptions.ClientError as e:
            log(f"Error validating Transit Gateway: {e}")
            return None  # Skip further TGW-related operations

        # Create or retrieve Key Pair
        try:
            key_name = f"{config['customer_code']}-key"
            existing_keys = ec2.describe_key_pairs()['KeyPairs']
            if not any(k['KeyName'] == key_name for k in existing_keys):
                key_pair = ec2.create_key_pair(KeyName=key_name)
                with open(f"{key_name}.pem", "w") as key_file:
                    key_file.write(key_pair['KeyMaterial'])
                log(f"Created Key Pair: {key_name}")
            else:
                log(f"Key Pair {key_name} already exists.")
        except Exception as e:
            log(f"Error creating or retrieving Key Pair: {e}")
            raise

        # Create VPC
        vpc = ec2.create_vpc(CidrBlock="192.168.0.0/16")
        vpc_id = vpc['Vpc']['VpcId']
        ec2.create_tags(Resources=[vpc_id], Tags=[
            {'Key': 'Customer', 'Value': config['customer_code']},
            {'Key': 'Name', 'Value': f"{config['customer_code']}-vpc"}
        ])
        log(f"Created VPC: {vpc_id} with name {config['customer_code']}-vpc")

        # Retrieve all availability zones
        azs = ec2.describe_availability_zones()['AvailabilityZones']
        az_list = [az['ZoneName'] for az in azs]
        log(f"Available AZs: {az_list}")

        # Create Subnets for each environment in unique AZs
        subnets = {}
        for i, env in enumerate(config['environments']):
            cidr_block = f"192.168.{i}.0/24"
            az = az_list[i % len(az_list)]  # Assign AZs in a round-robin fashion
            subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr_block, AvailabilityZone=az)
            subnet_id = subnet['Subnet']['SubnetId']
            ec2.create_tags(Resources=[subnet_id], Tags=[
                {'Key': 'Customer', 'Value': config['customer_code']},
                {'Key': 'Environment', 'Value': env['name']}
            ])
            log(f"Created Subnet for {env['name']} in AZ {az}: {subnet_id} with CIDR {cidr_block}")
            subnets[env['name']] = subnet_id

        # Attach VPC to Transit Gateway
        try:
            tgw_attachment = tgw.create_transit_gateway_vpc_attachment(
                TransitGatewayId=transit_gateway_id,
                VpcId=vpc_id,
                SubnetIds=list(subnets.values()),
                TagSpecifications=[
                    {
                        'ResourceType': 'transit-gateway-attachment',
                        'Tags': [
                            {'Key': 'Customer', 'Value': config['customer_code']}
                        ]
                    }
                ]
            )
            attachment_id = tgw_attachment['TransitGatewayVpcAttachment']['TransitGatewayAttachmentId']
            log(f"Attached VPC {vpc_id} to Transit Gateway with attachment ID: {attachment_id}")
        except Exception as e:
            log(f"Failed to attach VPC to Transit Gateway: {e}")
            raise

        # Create Security Group
        sg = ec2.create_security_group(GroupName=f"{config['customer_code']}-sg",
                                       Description="Customer Security Group",
                                       VpcId=vpc_id)
        security_group_id = sg['GroupId']
        ec2.create_tags(Resources=[security_group_id], Tags=[{'Key': 'Customer', 'Value': config['customer_code']}])
        log(f"Created Security Group: {security_group_id}")

        # Add rules to Security Group
        for port in config['allowed_ports']:
            ec2.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpProtocol="tcp",
                FromPort=port,
                ToPort=port,
                CidrIp="0.0.0.0/0"
            )
        log(f"Configured Security Group with ports: {config['allowed_ports']}")

        return {
            "vpc_id": vpc_id,
            "subnets": subnets,
            "security_group_id": security_group_id,
            "transit_gateway_attachment_id": attachment_id,
            "key_name": key_name
        }
    except Exception as e:
        log(f"Error creating VPC with Transit Gateway: {e}")
        raise

def generate_cloudformation_template(config, vpc_resources):
    """Generate a CloudFormation template based on VPC and related resources."""
    log("Generating CloudFormation template...")
    
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": {}
    }

    # Add VPC
    template["Resources"]["VPC"] = {
        "Type": "AWS::EC2::VPC",
        "Properties": {
            "CidrBlock": config.get("vpc_cidr", "192.168.0.0/16"),
            "Tags": [{"Key": "Customer", "Value": config["customer_code"]}]
        }
    }

    # Add Subnets
    for subnet_id, subnet_data in vpc_resources.get("subnets", {}).items():
        template["Resources"][f"Subnet{subnet_id}"] = {
            "Type": "AWS::EC2::Subnet",
            "Properties": {
                "VpcId": {"Ref": "VPC"},
                "CidrBlock": subnet_data["CidrBlock"],
                "AvailabilityZone": subnet_data["AvailabilityZone"],
                "Tags": [{"Key": "Customer", "Value": config["customer_code"]}]
            }
        }

    # Add Security Groups
    for sg_id, sg_data in vpc_resources.get("security_groups", {}).items():
        template["Resources"][f"SecurityGroup{sg_id}"] = {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "VpcId": {"Ref": "VPC"},
                "GroupDescription": sg_data["Description"],
                "SecurityGroupIngress": sg_data["IngressRules"],
                "Tags": [{"Key": "Customer", "Value": config["customer_code"]}]
            }
        }

    # Add Instances
    for instance_id, instance_data in vpc_resources.get("instances", {}).items():
        template["Resources"][f"Instance{instance_id}"] = {
            "Type": "AWS::EC2::Instance",
            "Properties": {
                "InstanceType": instance_data["InstanceType"],
                "SubnetId": {"Ref": f"Subnet{instance_data["SubnetId"]}"},
                "ImageId": instance_data["ImageId"],
                "KeyName": instance_data["KeyName"],
                "Tags": [{"Key": "Customer", "Value": config["customer_code"]}]
            }
        }

    # Save template to file
    try:
        output_path = config.get("cloudformation_template_path", "cloudformation_template.json")
        with open(output_path, "w") as file:
            json.dump(template, file, indent=4)
        log(f"CloudFormation template generated successfully: {output_path}")
    except Exception as e:
        log(f"Error generating CloudFormation template: {e}")
        raise

def create_key_pair(config):
    """Create a unique key pair for the customer."""
    ec2 = boto3.client('ec2', region_name=config['region'])
    key_pair_name = f"{config['customer_code']}_key"

    try:
        # Check if the key pair already exists
        response = ec2.describe_key_pairs(KeyNames=[key_pair_name])
        logger.info(f"Key pair '{key_pair_name}' already exists.")
        return key_pair_name
    except ec2.exceptions.ClientError as e:
        if "InvalidKeyPair.NotFound" in str(e):
            # Create the key pair if it doesn't exist
            logger.info(f"Key pair '{key_pair_name}' not found. Creating it now.")
            key_pair = ec2.create_key_pair(KeyName=key_pair_name)
            key_material = key_pair['KeyMaterial']

            # Save the private key to a file
            private_key_path = f"{key_pair_name}.pem"
            with open(private_key_path, "w") as file:
                file.write(key_material)
            os.chmod(private_key_path, 0o400)  # Restrict permissions on the key file

            logger.info(f"Key pair '{key_pair_name}' created and saved as '{private_key_path}'.")
            return key_pair_name
        else:
            logger.error(f"Error checking key pair: {e}")
            raise

def create_db_subnet_group(config, subnets):
    """Create a DB Subnet Group for RDS instances."""
    rds = boto3.client('rds', region_name=config['region'])
    subnet_ids = list(subnets.values())  # Use subnet IDs from the VPC setup

    db_subnet_group_name = f"{config['customer_code']}_db_subnet_group"

    try:
        # Check if the DB subnet group already exists
        rds.describe_db_subnet_groups(DBSubnetGroupName=db_subnet_group_name)
        logger.info(f"DB Subnet Group '{db_subnet_group_name}' already exists.")
    except rds.exceptions.DBSubnetGroupNotFoundFault:
        # Create the DB subnet group
        try:
            rds.create_db_subnet_group(
                DBSubnetGroupName=db_subnet_group_name,
                SubnetIds=subnet_ids,
                DBSubnetGroupDescription=f"DB Subnet Group for {config['customer_code']}",
                Tags=[
                    {'Key': 'Customer', 'Value': config['customer_code']}
                ]
            )
            logger.info(f"Created DB Subnet Group '{db_subnet_group_name}' with subnets: {subnet_ids}")
        except Exception as e:
            logger.error(f"Error creating DB Subnet Group: {e}")
            raise

    return db_subnet_group_name

def create_secure_password():
    characters = string.ascii_letters + string.digits + "!@#$%^&*()-_+=<>?[]{}"
    password = ''.join(random.choices(characters, k=16))
    while (not any(c.isupper() for c in password) or
           not any(c.isdigit() for c in password) or
           not any(c in "!@#$%^&*()-_+=<>?[]{}" for c in password)):
        password = ''.join(random.choices(characters, k=16))
    return password
    
def create_iam_users(config):
    """Create IAM users for the customer with detailed permissions."""
    iam = boto3.client("iam")
    customer_code = config["customer_code"]

    for env in config["environments"]:
        env_name = env["name"]
        env_code = env["code"]
        
        for account_type in ["admin", "service", "promotion", "restricted"]:
            user_name = f"{customer_code}-{env_code}-{account_type}"
            try:
                # Create IAM user
                iam.create_user(UserName=user_name)
                log(f"Created IAM user: {user_name}")

                # Generate policy document based on account type
                policy_document = generate_policy_document(account_type, env_code, config)
                
                # Create an inline policy for the user
                policy_name = f"{user_name}-policy"
                iam.put_user_policy(
                    UserName=user_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document)
                )
                log(f"Attached policy to {user_name}")

                # Create login profile for the user
                password = generate_strong_password()
                iam.create_login_profile(
                    UserName=user_name,
                    Password=password,
                    PasswordResetRequired=True
                )
                log(f"Password for {user_name}: {password}")

            except iam.exceptions.EntityAlreadyExistsException:
                log(f"Error creating IAM user {user_name}: User already exists.")
            except Exception as e:
                log(f"Error creating IAM user {user_name}: {e}")

def generate_policy_document(account_type, env_code, config):
    """Generate IAM policy document based on account type and environment level."""
    customer_code = config["customer_code"]
    s3_arn_prefix = f"arn:aws:s3:::{customer_code}"
    file_server_arn_prefix = f"arn:aws:fsx:*:*:file-system/{customer_code}"

    policy = {
        "Version": "2012-10-17",
        "Statement": []
    }

    if account_type == "promotion":
        next_env_code = f"{int(env_code) - 1:02}"  # Calculate the next higher environment
        policy["Statement"].append({
            "Effect": "Allow",
            "Action": [
                "s3:*",
                "fsx:*"
            ],
            "Resource": [
                f"{s3_arn_prefix}-{env_code}/*",
                f"{file_server_arn_prefix}-{env_code}",
                f"{s3_arn_prefix}-{next_env_code}/*",
                f"{file_server_arn_prefix}-{next_env_code}"
            ]
        })

    elif account_type == "admin":
        policy["Statement"].append({
            "Effect": "Allow",
            "Action": "*",
            "Resource": f"{s3_arn_prefix}-{env_code}/*"
        })

    elif account_type == "restricted":
        policy["Statement"].append({
            "Effect": "Allow",
            "Action": [
                "s3:Get*",
                "s3:List*",
                "fsx:DescribeFileSystems"
            ],
            "Resource": [
                f"{s3_arn_prefix}-{env_code}/*",
                f"{file_server_arn_prefix}-{env_code}"
            ]
        })

    return policy
                
def create_service_accounts(config):
    """Create service, promotion, and restricted accounts with specific permissions."""
    iam = boto3.client('iam')

    for env in config['environments']:
        for account_type in ['service', 'promotion', 'restricted']:
            account_name = f"{config['customer_code']}-{env['code']}-{account_type}"

            try:
                user = iam.create_user(UserName=account_name)
                log(f"Created IAM user: {account_name}")

                password = os.urandom(16).hex()
                iam.create_login_profile(
                    UserName=account_name,
                    Password=password,
                    PasswordResetRequired=True
                )
                log(f"Password for {account_name}: {password}")

                policy_arn = config['permissions'][account_type]
                iam.attach_user_policy(
                    UserName=account_name,
                    PolicyArn=policy_arn
                )
                log(f"Attached policy {policy_arn} to {account_name}")

            except Exception as e:
                log(f"Error creating IAM user {account_name}: {e}")

def setup_postgres(config, vpc_resources):
    """Set up PostgreSQL backend or configure default on central node."""
    if config['use_aws_rds']:
        rds = boto3.client('rds', region_name=config['region'])

        try:
            # Create DB Subnet Group
            db_subnet_group_name = create_db_subnet_group(config, vpc_resources['subnets'])

            for env in config['environments']:
                db_identifier = f"{config['customer_code']}-{env['code']}-db"

                rds.create_db_instance(
                    DBInstanceIdentifier=db_identifier,
                    AllocatedStorage=20,
                    DBInstanceClass='db.t3.micro',
                    Engine='postgres',
                    MasterUsername=config['db_username'],
                    MasterUserPassword=config['db_password'],
                    VpcSecurityGroupIds=[vpc_resources['security_group_id']],
                    DBSubnetGroupName=db_subnet_group_name,
                    Tags=[
                        {'Key': 'Customer', 'Value': config['customer_code']},
                        {'Key': 'Environment', 'Value': env['code']}
                    ]
                )
                logger.info(f"Created PostgreSQL instance: {db_identifier}")

        except Exception as e:
            logger.error(f"Error setting up PostgreSQL on RDS: {e}")
    else:
        logger.info("Using default PostgreSQL setup on the central node.")

def create_ec2_instances(config, vpc_resources):
    """Create EC2 instances for the customer's environments."""
    ec2 = boto3.client('ec2', region_name=config['region'])

    # Ensure the key pair exists or create it
    key_name = f"{config['customer_code']}-key"
    try:
        existing_keys = ec2.describe_key_pairs(KeyNames=[key_name])
        log(f"Key Pair {key_name} already exists.")
    except botocore.exceptions.ClientError as e:
        if 'InvalidKeyPair.NotFound' in str(e):
            log(f"Key Pair {key_name} not found. Creating new key pair.")
            key_pair = ec2.create_key_pair(KeyName=key_name)
            with open(f"{key_name}.pem", "w") as key_file:
                key_file.write(key_pair['KeyMaterial'])
            log(f"Created Key Pair: {key_name}")
        else:
            log(f"Unexpected error while checking key pairs: {e}")
            raise

    for env in config['environments']:
        env_name = env['name']
        subnet_id = vpc_resources['subnets'][env_name]
        for node in env['nodes']:
            try:
                # Validate instance parameters
                if 'instance_type' not in node or 'ami_id' not in node:
                    log(f"Error: Missing 'instance_type' or 'ami_id' for {node['type']} in {env_name}")
                    continue

                instance_type = node['instance_type']
                ami_id = node['ami_id']
                tags = [
                    {'Key': 'Customer', 'Value': config['customer_code']},
                    {'Key': 'Environment', 'Value': env_name},
                    {'Key': 'Node', 'Value': node['type']}
                ]

                instance = ec2.run_instances(
                    ImageId=ami_id,
                    InstanceType=instance_type,
                    KeyName=key_name,
                    SubnetId=subnet_id,
                    MinCount=1,
                    MaxCount=1,
                    TagSpecifications=[
                        {
                            'ResourceType': 'instance',
                            'Tags': tags
                        }
                    ]
                )
                instance_id = instance['Instances'][0]['InstanceId']
                log(f"Launched EC2 instance {instance_id} for {node['type']} in {env_name}")
            except Exception as e:
                log(f"Error launching EC2 instance for {node['type']} in {env_name}: {e}")

def setup_budgeting(config):
    setup_budget(
        customer_code=config['customer_code'],
        region=config['region'],
        account_id=config['account_id']
    )

def setup_budget(customer_code, region, account_id):
    """Set up budget alerts for a specific customer."""
    budgets = boto3.client('budgets', region_name=region)

    budget_name = f"{customer_code}-budget"
    log(f"Setting up budget: {budget_name}")

    try:
        budgets.create_budget(
            AccountId=account_id,
            Budget={
                'BudgetName': budget_name,
                'BudgetLimit': {
                    'Amount': '1000',  # Adjust as needed
                    'Unit': 'USD'
                },
                'TimeUnit': 'MONTHLY',
                'BudgetType': 'COST',
                'CostFilters': {},
                'CostTypes': {
                    'IncludeTax': True,
                    'IncludeSubscription': True,
                    'UseBlended': False
                }
            },
            NotificationsWithSubscribers=[
                {
                    'Notification': {
                        'NotificationType': 'ACTUAL',
                        'ComparisonOperator': 'GREATER_THAN',
                        'Threshold': 80.0,
                        'ThresholdType': 'PERCENTAGE',
                        'NotificationState': 'ALARM'
                    },
                    'Subscribers': [
                        {
                            'SubscriptionType': 'EMAIL',
                            'Address': 'billing@example.com'  # Adjust as needed
                        }
                    ]
                }
            ]
        )
        log(f"Budget {budget_name} created successfully.")
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateRecordException':
            log(f"Budget {budget_name} already exists. Skipping creation.")
        else:
            log(f"Failed to create budget {budget_name}: {e}")

def delete_budget(customer_code, region, account_id):
    """Delete budgets associated with a specific customer."""
    budgets = boto3.client('budgets', region_name=region)

    try:
        response = budgets.describe_budgets(AccountId=account_id)
        for budget in response.get('Budgets', []):
            if budget['BudgetName'].startswith(customer_code):
                try:
                    budgets.delete_budget(AccountId=account_id, BudgetName=budget['BudgetName'])
                    log(f"Deleted Budget: {budget['BudgetName']}")
                except Exception as e:
                    log(f"Failed to delete budget {budget['BudgetName']}: {e}")
    except Exception as e:
        log(f"Failed to fetch or delete budgets: {e}")

def load_config(config_file):
    """Load configuration from YAML file."""
    with open(config_file, 'r') as file:
        return yaml.safe_load(file)

def main():
    config = load_config('config.yaml')
    customer_code = config['customer_code']
    region = config['region']
    install_dependencies()
    if config.get('delete_resources', True):
        delete_customer_resources(customer_code, region, config)
    vpc_resources = create_vpc_with_tgw(config)
    create_iam_users(config)
    create_service_accounts(config)
    create_ec2_instances(config, vpc_resources)
    setup_postgres(config, vpc_resources)
    setup_budget(customer_code=config['customer_code'], region=config['region'], account_id=config['account_id'])
    generate_cloudformation_template(config, vpc_resources)

if __name__ == "__main__":
    main()

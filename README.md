# QlikSenseOnPremRapidOnboarder
Welcome to the Qlik Sense On Premise Rapid Onboarder.  The goal of this script is to standup and deploy all needed AWS resources to host a Qlik Sense server solution on AWS in as short amount of time as possible.  This script is currently in test mode.  It uses small ec2 nodes not normally designed to handle full Qlik Sense BI server specs.

File Descriptions:
Config.YAML - Contains the global setup and configuration parameters.

Delete.py - Deletes an entire implementation.  Used to cleanup everything after testing to prevent unwanted AWS hosting charges.

Main.py - Contains the primary process.

Requirements.txt - Contains all the dependent libraries needed to start the process.

Run Script.txt - Contains MSDOS script that executes the Python scripts.

Setup.py - Contains the installation of libraries and the AWSCLI client which are needed to execute the process.


Looking to contribute?  Happy to have you.  DM me for more info.

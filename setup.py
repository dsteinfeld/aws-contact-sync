"""Setup configuration for AWS Contact Synchronization."""

from setuptools import setup, find_packages

setup(
    name="aws-contact-sync",
    version="0.1.0",
    description="AWS Contact Synchronization System",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "boto3>=1.34.0",
    ],
    extras_require={
        "test": [
            "pytest>=7.4.0",
            "hypothesis>=6.88.0",
            "pytest-asyncio>=0.21.0",
        ]
    }
)
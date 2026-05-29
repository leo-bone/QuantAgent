from setuptools import setup, find_packages

setup(
    name="quantagent",
    version="0.1.0",
    description="AI-powered on-chain quantitative trading agent supporting Solana + BSC",
    author="Bill",
    python_requires=">=3.11",
    packages=find_packages(),
    install_requires=[],
    entry_points={
        "console_scripts": [
            "quantagent=quantagent.cli:main",
        ],
    },
)

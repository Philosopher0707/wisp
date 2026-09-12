from setuptools import setup, find_packages

setup(
    name="wisp",
    version="0.1.0",
    description="A local Ollama-powered coding agent — compatible with Warp's Skill ecosystem",
    author="Wisp Contributors",
    license="MIT",
    packages=find_packages(include=["wisp*"]),
    python_requires=">=3.10",
    install_requires=[
        "requests>=2.28",
        "pyyaml>=6.0",
        "fastapi>=0.110",
        "uvicorn>=0.29",
        "websockets>=12.0",
        "textual>=0.61",
        "filelock>=3.0",
        "numpy>=1.24",
        "jsonschema>=4.0",
        "aiohttp>=3.9",
        "tiktoken>=0.5",
        "prompt_toolkit>=3.0",
        "pydantic>=2.0",
        "cryptography>=41.0",
    ],
    entry_points={
        "console_scripts": [
            "wisp=wisp.__main__:main",
        ],
    },
)

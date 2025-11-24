from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="flacfetch",
    version="0.1.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="A CLI tool to fetch high-quality audio from various sources",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/flacfetch",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.28.0",
        "yt-dlp>=2023.0.0",
    ],
    extras_require={
        "torrent": ["libtorrent"],
        "dev": ["pytest", "pytest-mock", "mypy", "black", "ruff"],
    },
    entry_points={
        'console_scripts': [
            'flacfetch=flacfetch.interface.cli:main',
        ],
    },
)

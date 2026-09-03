from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="subbud",
    version="0.1.0",
    author="4riful",
    author_email="ariful@thexssrat.com",
    description="Redis-backed subdomain manager for bug bounty hunters, with a CLI and a TUI",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/4riful/subbud",
    project_urls={
        "Bug Tracker": "https://github.com/4riful/subbud/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    packages=find_packages(include=["subbud", "subbud.*"]),
    python_requires=">=3.8",
    install_requires=[
        "redis>=4.2",
        "python-dotenv>=0.19",
        "tqdm>=4.60",
        "textual>=0.40",
    ],
    entry_points={
        'console_scripts': [
            'subbud=subbud.main:main',
            'subbud-tui=subbud.tui:main',
        ],
    },
)

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="subbud",
    version="0.0.1",
    author="Your Name",
    author_email="your.email@example.com",
    description="A short description of the package",
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
    packages=find_packages(),
    python_requires=">=3.6",
)

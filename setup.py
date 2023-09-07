from setuptools import setup, find_packages

setup(
    name="subbud",
    version="1.0.2",
    description="Bug bounty subdomain management tool",
    author="XETTABYTE",
    author_email="ariful4nik@gmail.com",
    packages=find_packages(),
    install_requires=["redis"],
    entry_points={
        "console_scripts": [
            "subbud = subbud.main:main"
        ]
    },
)

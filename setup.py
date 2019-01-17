import os
from setuptools import setup, find_packages

with open(os.path.relpath(f"{__file__}/../README.md")) as f:
    readme = f.read()

setup(
    version="19.1",
    name="aapns",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.6",
    install_requires=["h2>3", "attrs", "structlog"],
    extras_require={"cli": ["click"]},
    entry_points={"console_scripts": ["aapns = aapns.cli:main"]},
    author="Jonas Obrist",
    author_email="ojiidotch@gmail.com",
    description="Asynchronous Apple Push Notification Service client",
    long_description=readme,
    long_description_content_type="text/markdown",
    url="https://github.com/HDE/aapns",
    project_urls={
        "Documentation": "https://aapns.readthedocs.io/en/latest/",
        "Code": "https://github.com/HDE/aapns",
        "Issue tracker": "https://github.com/HDE/aapns/issues",
    },
    license="APLv2",
    classifiers=[
        "Framework :: AsyncIO",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Apache Software License",
    ],
)

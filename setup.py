from setuptools import setup, find_packages


setup(
    version='0.1',
    name='aapns',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    install_requires=[
        'h2',
        'attrs',
        'structlog',
    ],
    extras_require={
        'cli': ['click']
    },
    entry_points={
        'console_scripts': [
            'aapns = aapns.cli:main'
        ]
    }
)

from setuptools import setup, find_packages


setup(
    version='1.0.0.dev7',
    name='aapns',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    python_requires='>=3.6',
    install_requires=[
        'h2>3',
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
    },
    license='APLv2',
    classifiers=[
        'Framework :: AsyncIO',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3 :: Only',
        'License :: OSI Approved :: Apache Software License'
    ]
)

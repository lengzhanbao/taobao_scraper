# -*- coding: utf-8 -*-
"""
Setup script for Taobao Live Scraper
"""
from setuptools import setup, find_packages
from pathlib import Path
ROOT = Path(__file__).resolve().parent

# Read README
with (ROOT / 'README.md').open('r', encoding='utf-8') as f:
    long_description = f.read()

# Read requirements
with (ROOT / 'requirements.txt').open('r', encoding='utf-8') as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name='taobao-live-scraper',
    version='2.6.0',
    author='Taobao Live Scraper Contributors',
    author_email='your.email@example.com',
    description='A professional tool for Taobao live streaming data collection and analysis',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/lengzhanbao/taobao_scraper',
    packages=find_packages(include=['src', 'src.*']),
    package_data={'src.control': ['web/*.html', 'web/*.js', 'web/*.css']},
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Scientific/Engineering :: Information Analysis',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.9',
    install_requires=requirements,
    keywords=[
        'taobao', 'live', 'scraper', 'crawler', 'e-commerce',
        'digital-human', 'data-collection', 'livestream',
        '淘宝', '直播', '爬虫', '数字人', '数据采集'
    ],
    project_urls={
        'Bug Reports': 'https://github.com/lengzhanbao/taobao_scraper/issues',
        'Source': 'https://github.com/lengzhanbao/taobao_scraper',
        'Documentation': 'https://github.com/lengzhanbao/taobao_scraper/blob/main/README.md',
    },
)

from setuptools import find_packages, setup

setup(
    name='github_trending_bot',
    version='0.1.5',
    install_requires=[
        'requests==2.12.4',
        'cachetools==2.0.0',
    ],
    entry_points={
        'console_scripts': [
            'github_trending_bot = github_trending_bot.bot:main',
        ]
    },
    tests_require=[
        'pytest==3.0.5',
        'responses==0.5.1',
    ],
    packages=find_packages(),
)

import pathlib
import re

from setuptools import setup, find_packages

name = 'aiohttp-boilerplate'

here = pathlib.Path(__file__).parent

txt = (here / 'aiohttp_boilerplate' / '__init__.py').read_text('utf-8')
install_requires = (here / 'requirements.txt').read_text('utf-8')
long_description = (here / 'README.md').read_text('utf-8')

try:
    version = re.findall(r"^__version__ = '([^']+)'\r?$", txt, re.M)[0]
except IndexError:
    raise RuntimeError('Unable to determine version.')

setup(
    name=name,
    version=version,
    python_requires='>=3.11',
    install_requires=install_requires,
    packages=find_packages(),
    license='MIT',
    author='Pro Webdevelop LLC',
    author_email='vladka@webdevelop.pro',
    description='A small aiohttp service framework',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/webdeveloppro/aiohttp_boilerplate',
    classifiers=[
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        "Operating System :: OS Independent",
    ],
    package_data={
    }
)

import pathlib
from setuptools import setup, find_packages

basedir = pathlib.Path(__file__).parent
reqs_file = basedir / 'requirements.txt'
deps = reqs_file.read_text().split('\n')

setup(name='toolkitrc-report',
      version='0.1.0',
      description='Report generator for ToolkitRC log files',
      author='didim99',
      install_requires=deps,
      packages=find_packages(),
      zip_safe=False,
      entry_points={
            'console_scripts': [
                  'toolkitrc-report = toolkitrc_report.cli:cli',
            ]
      })

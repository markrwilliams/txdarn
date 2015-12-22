from setuptools import setup, find_packages

setup(name='txdarn',
      version='0.0.1',
      packages=find_packages(),
      zip_safe=False,
      include_package_data=True,
      install_requires=['Twisted>=15.4.0',
                        'eliot',
                        'six',
                        'txWS',
                        'klein',
                        'automat'])

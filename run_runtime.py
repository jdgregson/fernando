import os
import runpy

os.environ['FERNANDO_ROLE'] = 'runtime'
runpy.run_module('run_fernando', run_name='__main__')

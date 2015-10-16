import os
import unittest
from op2 import OP2, rdpostop2

import pyNastran
from pyNastran.utils import object_attributes
test_path = pyNastran.__path__[0]

class TestOP2New(unittest.TestCase):
    def test_01(self):
        folder = os.path.abspath(os.path.join(test_path, '..', 'models'))
        op2_filename = os.path.join(folder, 'solid_bending', 'solid_bending.op2')

        op2 = OP2(op2_filename)
        op2.rdn2cop2()
        print(object_attributes(op2))
        print('dbnames =', op2.dbnames)
        print('dblist =', op2.dblist)


        rdpostop2(op2_filename, verbose=True, getougv1=False)

if __name__ == '__main__':
    unittest.main()

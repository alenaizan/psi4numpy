# A simple Psi 4 input script to compute MP2 from a RHF reference
# Requirements scipy 0.13.0+ and numpy 1.7.2+
#
# Algorithms were taken directly from Daniel Crawford's programming website:
# http://sirius.chem.vt.edu/wiki/doku.php?id=crawdad:programming
# Special thanks to Rob Parrish for initial assistance with libmints
#
# Created by: Daniel G. A. Smith
# Date: 7/29/14
# License: GPL v3.0
#

import time
import numpy as np
np.set_printoptions(precision=5, linewidth=200, suppress=True)


class helper_HF(object):

    def __init__(self, psi, energy, mol, memory=2, ndocc=None, scf_type='DF', guess='core'):

        # Build and all 2D values
        print('Building rank 2 integrals...')
        t = time.time()
        psi.set_active_molecule(mol)
        self.mints = psi.MintsHelper()
        self.enuc = mol.nuclear_repulsion_energy()

        mints = psi.MintsHelper()
        self.S = np.asarray(mints.ao_overlap())
        self.V = np.asarray(mints.ao_potential())
        self.T = np.asarray(mints.ao_kinetic())
        self.H = self.T + self.V

        self.Da = None
        self.Db = None
        self.Ca = None
        self.Cb = None

        self.J = None
        self.K = None

        # Orthoganlizer
        A = mints.ao_overlap()
        A.power(-0.5, 1.e-14)
        self.A = np.asarray(A)

        # Get nbf and ndocc for closed shell molecules
        self.epsilon = None
        self.nbf = self.S.shape[0]
        if ndocc:
            self.ndocc = ndocc
        else:
            self.ndocc = int(sum(mol.Z(A) for A in range(mol.natom())) / 2)

        # Only rhf for now
        self.nvirt = self.nbf - self.ndocc
        
        print('\nNumber of occupied orbitals: %d' % self.ndocc)
        print('Number of basis functions: %d' % self.nbf)

        self.C_left = psi.Matrix(self.nbf, self.ndocc)
        self.npC_left = np.asarray(self.C_left)

        if guess == 'core':
            Xp = self.A.dot(self.H).dot(self.A)
            e, C2 = np.linalg.eigh(Xp)
            C = self.A.dot(C2)
            self.npC_left[:] = C[:, :self.ndocc]
            self.epsilon = e 
            self.Da = np.dot(self.npC_left, self.npC_left.T)
        else:
            raise Exception("Guess %s not yet supported" % (guess))

        self.DIIS_error = []
        self.DIIS_F = []

        scf_type = scf_type.upper()
        if scf_type not in ['DF', 'PK', 'DIRECT']:
            raise Exception('SCF_TYPE %s not supported' % scf_type)
        psi.set_global_option('SCF_TYPE', scf_type)
        self.jk = psi.JK.build_JK()
        self.jk.initialize()
        self.jk.C_left().append(self.C_left)

        print('...rank 2 integrals built in %.3f seconds.' % (time.time() - t))

    def set_Cleft(self, C):
        if (C.shape[1] == self.ndocc):
            Cocc = C
        elif (C.shape[1] == self.nbf):
            self.Ca = C
            Cocc = C[:, :self.ndocc]
        else:
            raise Exception("Cocc shape is %s, need %s." % (str(self.npC_left.shape), str(Cocc.shape)))
        self.npC_left[:] = Cocc
        self.Da = np.dot(Cocc, Cocc.T)

    def diag(self, X, set_C=False):
        """
        Diaganolize with orthogonalizer A.
        """
        Xp = self.A.dot(X).dot(self.A)
        e, C2 = np.linalg.eigh(Xp)
        C = self.A.dot(C2)
        if set_C:
            self.set_Cleft(C)
        return e, C

    def build_fock(self):
        self.jk.compute()
        self.J = np.asarray(self.jk.J()[0])
        self.K = np.asarray(self.jk.K()[0])
        self.F = self.H + self.J * 2 - self.K
        return self.F

    def compute_hf_energy(self):
        self.scf_e = np.einsum('ij,ij->', self.F + self.H, self.Da) + self.enuc                
        return self.scf_e


class DIIS_helper(object):

    def __init__(self, max_vec=6):
        self.error = []
        self.vector = []
        self.max_vec = 6

    def add(self, matrix, error):

        if len(self.error) > 1:
            if self.error[-1].shape[0] != error.size:
                raise Exception("Error vector size does not match previous vector.")
            if self.vector[-1].shape != matrix.shape:
                raise Exception("Vector shape does not match previous vector.")

        self.error.append(error.ravel().copy())
        self.vector.append(matrix.copy())

    def extrapolate(self):
        # Limit size of DIIS vector
        diis_count = len(self.vector)

        if diis_count == 0:
            raise Exception("DIIS: No previous vectors.")
        if diis_count == 1:
            return self.vector[0]

        if diis_count > self.max_vec:
            # Remove oldest vector
            del self.vector[0]
            del self.error[0]
            diis_count -= 1

        # Build error matrix B
        B = np.empty((diis_count + 1, diis_count + 1))
        B[-1, :] = -1
        B[:, -1] = -1
        B[-1, -1] = 0
        for num1, e1 in enumerate(self.error):
            B[num1, num1] = np.dot(e1, e1)
            for num2, e2 in enumerate(self.error):
                if num2 >= num1: continue
                val = np.dot(e1, e2)
                B[num1, num2] = B[num2, num1] = val

        # normalize
        B[:-1, :-1] /= np.abs(B[:-1, :-1]).max()
        # Build residual vector
        resid = np.zeros(diis_count + 1)
        resid[-1] = -1

        # Solve pulay equations
        ci = np.linalg.solve(B, resid)
        # Calculate new fock matrix as linear
        # combination of previous fock matrices
        V = np.zeros_like(self.vector[-1])
        for num, c in enumerate(ci[:-1]):
            V += c * self.vector[num]

        return V
 
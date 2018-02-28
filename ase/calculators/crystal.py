"""This module defines an ASE interface to CRYSTAL14

http://www.crystal.unito.it/

Written by:

    Daniele Selli, daniele.selli@unimib.it
    Gianluca Fazio, g.fazio3@campus.unimib.it

The file 'fort.34' contains the input and output geometry
and it will be updated during the crystal calculations.
The wavefunction is stored in 'fort.20' as binary file.

The keywords are given, for instance, as follows:

    guess = True,
    xc = 'PBE',
    kpts = (2,2,2),
    otherkeys = [ 'scfdir', 'anderson', ['maxcycles','500'],
                 ['fmixing','90']],
    ...

"""

from ase.units import Hartree, Bohr
from ase.io import write
import numpy as np
import os

from ase.calculators.calculator import FileIOCalculator


class CRYSTAL(FileIOCalculator):
    """ A crystal calculator with ase-FileIOCalculator nomenclature
    """

    implemented_properties = ['energy', 'forces', 'stress', 'charges',
                              'dipole']

    def __init__(self, restart=None, ignore_bad_restart_file=False,
                 label='cry', atoms=None, **kwargs):
        """Construct a crystal calculator.

        """
        # default parameters
        self.default_parameters = dict(
            xc='HF',
            spinpol=False,
            guess=True,
            kpts=None,
            isp=1,
            basis='custom',
            smearing=None,
            otherkeys=[])

        self.pcpot = None
        self.lines = None
        self.atoms = None
        self.atoms_input = None
        self.outfilename = 'cry.out'

        FileIOCalculator.__init__(self, restart, ignore_bad_restart_file,
                                  label, atoms,
                                  **kwargs)

    def write_crystal_in(self, filename):
        """ Write the input file for the crystal calculation.
            Geometry is taken always from the file 'fort.34'
        """

        # write BLOCK 1 (only SP with gradients)
        outfile = open(filename, 'w')
        outfile.write('Single point + Gradient crystal calculation \n')
        outfile.write('EXTERNAL \n')
        if self.pcpot:
            outfile.write('POINTCHG \n')
            self.pcpot.write_mmcharges('POINTCHG.INP')

        # write BLOCK 2 from file (basis sets)
        p = self.parameters
        if p.basis == 'custom':
            outfile.write('END \n')
            basisfile = open(os.path.join(self.directory, 'basis'))
            basis_ = basisfile.readlines()
            for line in basis_:
                outfile.write(line)
            outfile.write('99 0 \n')
            outfile.write('END \n')
        else:
            outfile.write('BASISSET \n')
            outfile.write(p.basis.upper() + '\n')

        # write BLOCK 3 according to parameters set as input
        # ----- write hamiltonian

        if self.atoms.get_initial_magnetic_moments().any():
            p.spinpol = True

        if p.xc == 'HF':
            if p.spinpol:
                outfile.write('UHF \n')
            else:
                outfile.write('RHF \n')
        elif p.xc == 'MP2':
            outfile.write('MP2 \n')
            outfile.write('ENDMP2 \n')
        else:
            outfile.write('DFT \n')
            # Standalone keywords and LDA are given by a single string.
            if isinstance(p.xc, str):
                xc = {'LDA': 'EXCHANGE\nLDA\nCORRELAT\nVWN',
                      'PBE': 'PBEXC'}.get(p.xc, p.xc)
                outfile.write(xc.upper() + '\n')
        # Custom xc functional are given by a tuple of string
            else:
                x, c = p.xc
                outfile.write('EXCHANGE \n')
                outfile.write(x + ' \n')
                outfile.write('CORRELAT \n')
                outfile.write(c + ' \n')
            if p.spinpol:
                outfile.write('SPIN \n')
            outfile.write('END \n')
        # When guess=True, wf is read.
        if p.guess:
            # wf will be always there after 2nd step.
            if os.path.isfile('fort.20'):
                outfile.write('GUESSP \n')

        # smearing
        if p.smearing is not None:
            if p.smearing[0] != 'Fermi-Dirac':
                raise ValueError('Only Fermi-Dirac smearing is allowed.')
            else:
                outfile.write('SMEAR \n')
                outfile.write(str(p.smearing[1] / Hartree) + ' \n')

        # ----- write other CRYSTAL keywords
        # ----- in the list otherkey = ['ANDERSON', ...] .

        for keyword in p.otherkeys:
            if isinstance(keyword, str):
                outfile.write(keyword.upper() + '\n')
            else:
                for key in keyword:
                    outfile.write(key.upper() + '\n')

        ispbc = self.atoms.get_pbc()
        self.kpts = p.kpts

        # if it is periodic, gamma is the default.
        if any(ispbc):
            if self.kpts is None:
                self.kpts = (1, 1, 1)
        else:
            self.kpts = None

        # explicit lists of K-points, shifted Monkhorst-
        # Pack net and k-point density definition are
        # not allowed.
        if self.kpts is not None:
            if isinstance(self.kpts, float):
                raise ValueError('K-point density definition not allowed.')
            if isinstance(self.kpts, list):
                raise ValueError('Explicit K-points definition not allowed.')
            if isinstance(self.kpts[-1], str):
                raise ValueError('Shifted Monkhorst-Pack not allowed.')
            outfile.write('SHRINK  \n')
            # isp is by default 1, 2 is suggested for metals.
            outfile.write('0 ' + str(p.isp * max(self.kpts)) + ' \n')
            if ispbc[2]:
                outfile.write(str(self.kpts[0])
                              + ' ' + str(self.kpts[1])
                              + ' ' + str(self.kpts[2]) + ' \n')
            elif ispbc[1]:
                outfile.write(str(self.kpts[0])
                              + ' ' + str(self.kpts[1])
                              + ' 1 \n')
            elif ispbc[0]:
                outfile.write(str(self.kpts[0])
                              + ' 1 1 \n')

        # GRADCAL command performs a single
        # point and prints out the forces
        # also on the charges
        outfile.write('GRADCAL \n')
        outfile.write('END \n')

        outfile.close()

    def write_input(self, atoms, properties=None, system_changes=None):
        FileIOCalculator.write_input(
            self, atoms, properties, system_changes)
        self.write_crystal_in(os.path.join(self.directory, 'INPUT'))
        write(os.path.join(self.directory, 'fort.34'), atoms)
        # self.atoms is none until results are read out,
        # then it is set to the ones at writing input
        self.atoms_input = atoms
        self.atoms = None

    def read_results(self):
        """ all results are read from OUTPUT file
            It will be destroyed after it is read to avoid
            reading it once again after some runtime error """

        with open(os.path.join(self.directory, 'OUTPUT'), 'r') as myfile:
            self.lines = myfile.readlines()

        self.atoms = self.atoms_input
        # Energy line index
        estring1 = 'SCF ENDED'
        estring2 = 'TOTAL ENERGY + DISP'
        for iline, line in enumerate(self.lines):
            if line.find(estring1) >= 0:
                index_energy = iline
                pos_en = 8
                break
        else:
            raise RuntimeError('Problem in reading energy')
        # Check if there is dispersion corrected
        # energy value.
        for iline, line in enumerate(self.lines):
            if line.find(estring2) >= 0:
                index_energy = iline
                pos_en = 5
        energy = float(self.lines[index_energy].split()[pos_en]) * Hartree
        self.results['energy'] = energy

        # Force line indexes
        fstring = 'CARTESIAN FORCES'
        gradients = []
        for iline, line in enumerate(self.lines):
            if line.find(fstring) >= 0:
                index_force_begin = iline + 2
                break
        else:
            raise RuntimeError('Problem in reading forces')
        for j in range(index_force_begin, index_force_begin + len(self.atoms)):
            word = self.lines[j].split()
            gradients.append([float(word[k + 2]) for k in range(0, 3)])
        forces = np.array(gradients) * Hartree / Bohr

        self.results['forces'] = forces

        # stress stuff begins
        sstring = 'STRESS TENSOR, IN'
        have_stress = False
        stress = []
        for iline, line in enumerate(self.lines):
            if sstring in line:
                have_stress = True
                start = iline + 4
                end = start + 3
                for i in range(start, end):
                    cell = [float(x) for x in self.lines[i].split()]
                    stress.append(cell)
        if have_stress:
            stress = -np.array(stress) * Hartree / Bohr**3
            self.results['stress'] = stress

        # stress stuff ends

        # Get partial charges on atoms.
        # In case we cannot find charges
        # they are set to None
        qm_charges = []

        # ----- this for cycle finds the last entry of the
        # ----- string search, which corresponds
        # ----- to the charges at the end of the SCF.
        for n, line in enumerate(self.lines):
            if 'TOTAL ATOMIC CHARGE' in line:
                chargestart = n + 1
        lines1 = self.lines[chargestart:(chargestart
                                         + (len(self.atoms) - 1) // 6 + 1)]
        atomnum = self.atoms.get_atomic_numbers()
        words = []
        for line in lines1:
            for el in line.split():
                words.append(float(el))
        i = 0
        for atn in atomnum:
            qm_charges.append(-words[i] + atn)
            i = i + 1
        charges = np.array(qm_charges)
        self.results['charges'] = charges

        # Read dipole moment.

        dipole = np.zeros([1, 3])
        for n, line in enumerate(self.lines):
            if 'DIPOLE MOMENT ALONG' in line:
                dipolestart = n + 2
                dipole = np.array([float(f) for f in
                                   self.lines[dipolestart].split()[2:5]])
                break
        # debye to e*Ang
        self.results['dipole'] = dipole * 0.2081943482534

    def embed(self, mmcharges=None, directory='./'):
        """Embed atoms in point-charges (mmcharges)
        """
        self.pcpot = PointChargePotential(mmcharges, self.directory)
        return self.pcpot


class PointChargePotential:
    def __init__(self, mmcharges, directory='./'):
        """Point-charge potential for CRYSTAL.
        """
        self.mmcharges = mmcharges
        self.directory = directory
        self.mmpositions = None
        self.mmforces = None

    def set_positions(self, mmpositions):
        self.mmpositions = mmpositions

    def set_charges(self, mmcharges):
        self.mmcharges = mmcharges

    def write_mmcharges(self, filename='POINTCHG.INP'):
        """ mok all
        write external charges as monopoles for CRYSTAL.

        """
        if self.mmcharges is None:
            print("CRYSTAL: Warning: not writing external charges ")
            return
        charge_file = open(os.path.join(self.directory, filename), 'w')
        charge_file.write(str(len(self.mmcharges)) + ' \n')
        for [pos, charge] in zip(self.mmpositions, self.mmcharges):
            [x, y, z] = pos
            charge_file.write('%12.6f %12.6f %12.6f %12.6f \n'
                              % (x, y, z, charge))
        charge_file.close()

    def get_forces(self, calc, get_forces=True):
        """ returns forces on point charges if the flag get_forces=True """
        if get_forces:
            return self.read_forces_on_pointcharges()
        else:
            return np.zeros_like(self.mmpositions)

    def read_forces_on_pointcharges(self):
        """Read Forces from CRYSTAL output file (OUTPUT)."""
        from ase.units import Hartree, Bohr
        infile = open(os.path.join(self.directory, 'OUTPUT'), 'r')
        lines = infile.readlines()
        infile.close()

        external_forces = []
        for n, line in enumerate(lines):
            if ('RESULTANT FORCE' in line):
                chargeend = n - 1
                break
        else:
            raise RuntimeError(
                'Problem in reading forces on MM external-charges')
        lines1 = lines[(chargeend - len(self.mmcharges)):chargeend]
        for line in lines1:
            external_forces.append(
                [float(i) for i in line.split()[2:]])
        return np.array(external_forces) * Hartree / Bohr
import re
import warnings
from collections.abc import Iterable
from copy import deepcopy

import numpy as np

from ase import Atoms
from ase.units import Hartree, Bohr
from ase.calculators.calculator import InputError
from ase.calculators.singlepoint import SinglePointCalculator
from ase.calculators.gaussian import Gaussian
from ase.data import chemical_symbols, atomic_masses_iupac2016

from ase.io.zmatrix import parse_zmatrix

_link0_keys = [
    'mem',
    'chk',
    'oldchk',
    'schk',
    'rwf',
    'oldmatrix',
    'oldrawmatrix',
    'int',
    'd2e',
    'save',
    'nosave',
    'errorsave',
    'cpu',
    'nprocshared',
    'gpucpu',
    'lindaworkers',
    'usessh',
    'ssh',
    'debuglinda',
]


_link0_special = [
    'kjob',
    'subst',
]


# Certain problematic methods do not provide well-defined potential energy
# surfaces, because these "composite" methods involve geometry optimization
# and/or vibrational frequency analysis. In addition, the "energy" calculated
# by these methods are typically ZPVE corrected and/or temperature dependent
# free energies.
_problem_methods = [
    'cbs-4m', 'cbs-qb3', 'cbs-apno',
    'g1', 'g2', 'g3', 'g4', 'g2mp2', 'g3mp2', 'g3b3', 'g3mp2b3', 'g4mp4',
    'w1', 'w1u', 'w1bd', 'w1ro',
]


_xc_to_method = dict(
    pbe='pbepbe',
    pbe0='pbe1pbe',
    hse06='hseh1pbe',
    hse03='ohse2pbe',
    lda='svwn',  # gaussian "knows about" LSDA, but maybe not LDA.
    tpss='tpsstpss',
    revtpss='revtpssrevtpss',
)

_nuclei_prop_array_names = ['spin', 'zeff', 'qmom', 'nmagm', 'znuc',
                            'radnuclear', 'iso']


def write_gaussian_in(fd, atoms, properties=None, **params):
    '''
    Generates a Gaussian input file

    Parameters
    -----------
    fd: file-like
        where the Gaussian input file will be written
    atoms: Atoms
        Structure to write to the input file
    properties: list
        Properties to calculate
    params: dict
        Contains information about the Gaussian calculation,
        including the method and basis, charge and multiplicity,
        link 0 commands and route section keywords and options.

    '''

    params = deepcopy(params)

    if properties is None:
        properties = ['energy']

    # pop method and basis and output type
    method = params.pop('method', None)
    basis = params.pop('basis', None)
    fitting_basis = params.pop('fitting_basis', None)
    output_type = '#{}'.format(params.pop('output_type', 'P'))
    # Change output type to P if it has been set to T, because ASE
    # does not support reading info from 'terse' output files.
    if output_type == '#' or 't' in output_type.lower():
        output_type = '#P'

    # basisfile or basis_set, only used if basis=gen
    basisfile = params.pop('basisfile', None)
    basis_set = params.pop('basis_set', None)

    # basis can be omitted if basisfile is provided
    if basis is None:
        if basisfile is not None or basis_set is not None:
            basis = 'gen'

    # determine method from xc if it is provided
    if method is None:
        xc = params.pop('xc', None)
        if xc is not None:
            method = _xc_to_method.get(xc.lower(), xc)

    # If the user requests a problematic method, rather than raising an error
    # or proceeding blindly, give the user a warning that the results parsed
    # by ASE may not be meaningful.
    if method is not None:
        if method.lower() in _problem_methods:
            warnings.warn(
                'The requested method, {}, is a composite method. Composite '
                'methods do not have well-defined potential energy surfaces, '
                'so the energies, forces, and other properties returned by '
                'ASE may not be meaningful, or they may correspond to a '
                'different geometry than the one provided. '
                'Please use these methods with caution.'.format(method)
            )

    # determine charge from initial charges if not passed explicitly
    charge = params.pop('charge', None)
    if charge is None:
        charge = atoms.get_initial_charges().sum()

    # determine multiplicity from initial magnetic moments
    # if not passed explicitly
    mult = params.pop('mult', None)
    if mult is None:
        mult = atoms.get_initial_magnetic_moments().sum() + 1

    # pull out raw list of explicit keywords for backwards compatibility
    extra = params.pop('extra', None)

    # pull out any explicit IOPS
    ioplist = params.pop('ioplist', None)

    # also pull out 'addsec', which e.g. contains modredundant info
    addsec = params.pop('addsec', None)

    # pull out nuclei properties:
    nuclei_props = {}
    for keyword in _nuclei_prop_array_names:
        nuclei_props[keyword] = params.pop(keyword, None)
    nuclei_props.pop('iso')

    # set up link0 arguments
    out = []
    for key in _link0_keys:
        if key not in params:
            continue
        val = params.pop(key)
        if not val or (isinstance(val, str) and key.lower() == val.lower()):
            out.append('%{}'.format(key))
        else:
            out.append('%{}={}'.format(key, val))

    # These link0 keywords have a slightly different syntax
    for key in _link0_special:
        if key not in params:
            continue
        val = params.pop(key)
        if not isinstance(val, str) and isinstance(val, Iterable):
            val = ' '.join(val)
        out.append('%{} L{}'.format(key, val))

    # begin route line
    # note: unlike in old calculator, each route keyword is put on its own
    # line.
    if basis and method and fitting_basis:
        out.append('{} {}/{}/{} ! ASE formatted method and basis'
                   .format(output_type, method, basis, fitting_basis))
    elif basis and method:
        out.append('{} {}/{} ! ASE formatted method and basis'
                   .format(output_type, method, basis))
    else:
        output_string = '{}'.format(output_type)
        for value in [method, basis]:
            if value is not None:
                output_string += ' {}'.format(value)
        out.append(output_string)

    for key, val in params.items():
        # assume bare keyword if val is falsey, i.e. '', None, False, etc.
        # also, for backwards compatibility: assume bare keyword if key and
        # val are the same
        if not val or (isinstance(val, str) and key.lower() == val.lower()):
            out.append(key)
        elif not isinstance(val, str) and isinstance(val, Iterable):
            out.append('{}({})'.format(key, ','.join(val)))
        else:
            out.append('{}({})'.format(key, val))

    if ioplist is not None:
        out.append('IOP(' + ', '.join(ioplist) + ')')

    if extra is not None:
        out.append(extra)

    # Add 'force' iff the user requested forces, since Gaussian crashes when
    # 'force' is combined with certain other keywords such as opt and irc.
    if 'forces' in properties and 'force' not in params:
        out.append('force')

    # header, charge, and mult
    out += ['', 'Gaussian input prepared by ASE', '',
            '{:.0f} {:.0f}'.format(charge, mult)]

    # atomic positions and nuclear properties
    for i, atom in enumerate(atoms):

        symbol_section = atom.symbol + \
            '('
        # Check whether any nuclear properties of the atom have been set,
        # and if so, add them to the symbol section.
        nuclei_props_set = False
        for keyword, array in nuclei_props.items():
            if array is not None and array[i] is not None:
                string = keyword + '=' + str(array[i]) + ', '
                symbol_section += string
                nuclei_props_set = True

        # Check whether the mass of the atom has been modified,
        # and if so, add it to the symbol section:
        mass_set = False
        symbol = atom.symbol
        if symbol in chemical_symbols:
            expected_mass = atomic_masses_iupac2016[chemical_symbols.index(
                symbol)]
        else:
            expected_mass = None
        if expected_mass != atoms[i].mass:
            mass_set = True
            string = 'iso' + '=' + str(atoms[i].mass)
            symbol_section += string

        if nuclei_props_set or mass_set:
            symbol_section = symbol_section.strip(', ')
            symbol_section += ')'
        else:
            symbol_section = symbol_section.strip('(')

        # Then attach the properties appropriately
        # this formatting was chosen for backwards compatibility reasons, but
        # it would probably be better to
        # 1) Ensure proper spacing between entries with explicit spaces
        # 2) Use fewer columns for the element
        # 3) Use 'e' (scientific notation) instead of 'f' for positions
        out.append('{:<10s}{:20.10f}{:20.10f}{:20.10f}'.format(symbol_section,
                                                               *atom.position))

    # unit cell vectors, in case of periodic boundary conditions
    for ipbc, tv in zip(atoms.pbc, atoms.cell):
        if ipbc:
            out.append('TV {:20.10f}{:20.10f}{:20.10f}'.format(*tv))

    out.append('')

    # if basis='gen', set basisfile. Either give a path to a basisfile, or
    # read in the provided file and paste it verbatim
    if basisfile is not None:
        if basisfile[0] == '@':
            out.append(basisfile)
        else:
            with open(basisfile, 'r') as f:
                out.append(f.read())
    elif basis_set is not None:
        out.append(basis_set)
    else:
        if basis is not None and basis.lower() == 'gen':
            raise InputError('Please set basisfile or basis_set')

    if addsec is not None:
        out.append('')
        if isinstance(addsec, str):
            out.append(addsec)
        elif isinstance(addsec, Iterable):
            out += list(addsec)

    out += ['', '']
    fd.write('\n'.join(out))


# Regexp for reading an input file:

_re_link0 = re.compile(r'^\s*%([^\=\)\(!]+)=?([^\=\)\(!]+)?(!.+)?')
# Link0 lines are in the format:
# '% keyword = value' or '% keyword'
# (with or without whitespaces)

_re_output_type = re.compile(r'^\s*#\s*([NPTnpt]?)\s*')
# The start of the route section begins with a '#', and then may
# be followed by the desired level of output in the output file: P, N or T.

_re_method_basis = re.compile(
    r"\s*([\w-]+)\s*\/([^/=!]+)([\/]([^!]+))?\s*(!.+)?")
# Matches method, basis and optional fitting basis in the format:
# method/basis/fitting_basis ! comment
# They will appear in this format if the Gaussian file has been generated
# by ASE using a calculator with the basis and method keywords set.

_re_chgmult = re.compile(r'^\s*[+-]?\d+(?:,\s*|\s+)[+-]?\d+\s*$')
# This is a bit more complex of a regex than we typically want, but it
# can be difficult to determine whether a line contains the charge and
# multiplicity, rather than just another route keyword. By making sure
# that the line contains exactly two *integers*, separated by either
# a comma (and possibly whitespace) or some amount of whitespace, we
# can be more confident that we've actually found the charge and multiplicity.

# The following methods are used in GaussianConfiguration's
# parse_gaussian_input method:


def _save_link0_param(link0_match, parameters):
    '''Saves link0 keywords and options to the
    parameters dictionary '''
    value = link0_match.group(2)
    if value is not None:
        value = value.strip()
    parameters.update({link0_match.group(1).lower().strip():
                       value})


def _convert_to_symbol(string):
    '''Converts an input string into a format
    that can be input to the 'symbol' parameter of an
    ASE Atom object (can be a chemical symbol (str)
    or an atomic number (int).)
    This is achieved by either stripping any
    integers from the string, or converting a string
    containing an atomic number to integer type'''
    symbol = _validate_symbol_string(string)
    if symbol.isnumeric():
        atomic_number = int(symbol)
        symbol = chemical_symbols[atomic_number]
    else:
        match = re.match(r'([A-Za-z]+)', symbol)
        symbol = match.group(1)
    return symbol


def _validate_symbol_string(string):
    if "-" in string:
        raise IOError("ERROR: Could not read the Gaussian input file, as"
                      " molecule specifications for molecular mechanics "
                      "calculations are not supported.")
    return string


def _get_route_params(line):
    '''Reads a line of the route section of a gaussian input file.

    Parameters
    ----------
    line (string)
        A line of the route section of a gaussian input file.

    Returns
    ---------
    params (dict)
        Contains the keywords and options found in the line.
    '''
    params = {}
    line = line.strip(' #')
    line = line.split('!')[0]  # removes any comments
    # First, get the keywords and options sepatated with
    # parantheses:
    match_iterator = re.finditer(r'\(([^\)]+)\)', line)
    index_ranges = []
    for match in match_iterator:
        index_range = [match.start(0), match.end(0)]
        options = match.group(1)
        # keyword is last word in previous substring:
        keyword_string = line[:match.start(0)]
        keyword_match_iter = [k for k in re.finditer(
            r'[^\,/\s]+', keyword_string) if k.group() != '=']
        keyword = keyword_match_iter[-1].group().strip(' =')
        index_range[0] = keyword_match_iter[-1].start()
        params.update({keyword.lower(): options})
        index_ranges.append(index_range)

    # remove from the line the keywords and options that we have saved:
    index_ranges.reverse()
    for index_range in index_ranges:
        start = index_range[0]
        stop = index_range[1]
        line = line[0: start:] + line[stop + 1::]

    # Next, get the keywords and options separated with
    # an equals sign, and those without an equals sign
    # must be keywords without options:

    # remove any whitespaces around '=':
    line = re.sub(r'\s*=\s*', '=', line)
    line = [x for x in re.split(r'[\s,\/]', line) if x != '']

    for s in line:
        if '=' in s:
            s = s.split('=')
            keyword = s.pop(0)
            options = s.pop(0)
            for string in s:
                options += '=' + string
            params.update({keyword.lower(): options})
        else:
            if len(s) > 0:
                params.update({s.lower(): None})

    return params


def _save_route_params(line, parameters):
    '''Reads keywords and values from a line in
    a Gaussian input file's route section,
    and saves them to the parameters dictionary'''
    method_basis_match = _re_method_basis.match(line)
    if method_basis_match:
        ase_gen_comment = '! ASE formatted method and basis'
        if method_basis_match.group(5) == ase_gen_comment:
            parameters.update(
                {'method': method_basis_match.group(1).strip()})
            parameters.update(
                {'basis': method_basis_match.group(2).strip()})
            if method_basis_match.group(4):
                parameters.update(
                    {'fitting_basis': method_basis_match.group(4).strip()})
            return
    parameters.update(_get_route_params(line))


def _save_nuclei_props(line, nuclei_props):
    ''' Reads any info in parantheses in the line and stores
    it in the nuceli_props dict. Returns the line with
    the nuclei properties removed, along with the atom symbol
    and tokens which contains its position'''
    nuclei_props_match = re.search(r'\(([^\)]+)\)', line)
    if nuclei_props_match:
        line = line.replace(nuclei_props_match.group(0), '')
        tokens = line.split()
        symbol = _convert_to_symbol(
            tokens[0])
        current_nuclei_props = _get_route_params(nuclei_props_match.group(1))
        updated_current_nuclei_props = {}
        for k, v in current_nuclei_props.items():
            if v.isnumeric():
                v = int(v)
            else:
                v = float(v)
            updated_current_nuclei_props[k.lower()] = v

        current_nuclei_props = updated_current_nuclei_props

    else:
        tokens = line.split()
        symbol = _convert_to_symbol(tokens[0])
        current_nuclei_props = {}

    # Only save this info if the line is not defining PBCs:
    if symbol.upper() != 'TV':
        for k in nuclei_props:
            if k in current_nuclei_props.keys():
                nuclei_props[k].append(
                    current_nuclei_props.pop(k))
            else:
                nuclei_props[k].append(None)

        if current_nuclei_props != {}:
            for key, value in current_nuclei_props.items():
                if "fragment" in key.lower():
                    warnings.warn("Fragments are not"
                                  "currently supported.")

            warnings.warn("The following nuclei properties "
                          "could not be saved: {}".format(
                              current_nuclei_props))

    return line, [symbol, tokens]


def _add_nuclei_props_to_params(parameters, nuclei_props):
    ''' Attaches each nuclear property as a custom array
    in the atoms object'''
    params = deepcopy(parameters)
    for key in nuclei_props:
        values_set = False
        for value in nuclei_props[key]:
            if value is not None:
                values_set = True
        if values_set:
            params[key] = nuclei_props[key]
    return params


def _get_readiso_param(parameters):
    ''' Returns a dictionary containing the frequency
    keyword and its options, if the frequency keyword is
    present in parameters and ReadIso is one of its options'''
    freq_options = parameters.get('freq', None)
    if freq_options:
        freq_name = 'freq'
    else:
        freq_options = parameters.get('frequency', None)
        freq_name = 'frequency'
    if freq_options is not None:
        freq_options = freq_options.lower()
        if 'readiso' or 'readisotopes' in freq_options:
            return {freq_name: freq_options}


def _save_readiso_info(line, parameters):
    '''Reads the temperature, pressure and scale from the first line
    of a ReadIso section of a Gaussian input file. Saves these as
    route section parameters. Returns True if this info exists and
    has been saved.'''
    freq_param = _get_readiso_param(parameters)
    if freq_param is not None:
        # when count_iso is 0 we are in the line where
        # temperature, pressure, [scale] is saved
        line = line.replace(
            '[', '').replace(']', '')
        tokens = line.strip().split()
        try:
            parameters.update({'temperature': tokens[0]})
            parameters.update({'pressure': tokens[1]})
            parameters.update({'scale': tokens[2]})
        except IndexError:
            pass
        return True


def _delete_readiso_param(parameters):
    '''Removes the readiso parameter from the parameters dict'''
    freq_param = _get_readiso_param(parameters)
    if freq_param is not None:
        freq_name = [k for k in freq_param.keys()][0]
        freq_options = [v for v in freq_param.values()][0].lower()
        if 'readisotopes' in freq_options:
            iso_name = 'readisotopes'
        else:
            iso_name = 'readiso'
        freq_options = [v.group() for v in re.finditer(
            r'[^\,/\s]+', freq_options)]
        freq_options.remove(iso_name)
        new_freq_options = ''
        for v in freq_options:
            new_freq_options += v + ' '
        if new_freq_options == '':
            new_freq_options = None
        else:
            new_freq_options = new_freq_options.strip()
        parameters[freq_name] = new_freq_options


def _validate_params(parameters):
    '''Checks whether all of the required parameters exist in the
    parameters dict and whether it contains any unsupported settings
    '''
    # Check whether charge and multiplicity have been read.
    if 'charge' not in parameters.keys() or \
            'mult' not in parameters.keys():
        warnings.warn("Could not read the charge and multiplicity "
                      "from the Gaussian input file. These must be 2 "
                      "integers separated with whitespace or a comma.")

    # Check for unsupported settings
    unsupported_settings = [
        "Z-matrix", "ModRedun", "AddRedun", "ReadOpt", "RdOpt"]
    for s in unsupported_settings:
        for v in parameters.values():
            if v is not None:
                if s.lower() in str(v).lower():
                    raise IOError(
                        "ERROR: Could not read the Gaussian input file"
                        ", as the option: {} is currently unsupported."
                        .format(s))
    for k in parameters.keys():
        if "popt" in k.lower():
            parameters["Opt"] = parameters.pop(k)
            warnings.warn("The option {} is currently unsupported. "
                          "This has been replaced with {}."
                          .format("POpt", "Opt"))
            return


def _read_zmatrix(zmatrix_contents, zmatrix_vars):
    ''' Reads a z-matrix (zmatrix_contents) using its list of variables
    (zmatrix_vars), and returns atom positions and symbols '''
    try:
        if len(zmatrix_vars) > 0:
            atoms = parse_zmatrix(zmatrix_contents, defs=zmatrix_vars)
        else:
            atoms = parse_zmatrix(zmatrix_contents)
    except ValueError as e:
        raise IOError("Failed to read Z-matrix from "
                      "Gaussian input file: ", e)
    except KeyError as e:
        raise IOError("Failed to read Z-matrix from "
                      "Gaussian input file, as symbol: {}"
                      "could not be recognised. Please make "
                      "sure you use element symbols, not "
                      "atomic numbers in the element labels.".format(e))

    positions = atoms.positions
    symbols = atoms.get_chemical_symbols()
    return positions, symbols


class GaussianConfiguration:

    def __init__(self, atoms, parameters):
        self.atoms = atoms.copy()
        self.parameters = deepcopy(parameters)

    def get_atoms(self):
        return self.atoms

    def get_parameters(self):
        return self.parameters

    def get_calculator(self):
        calc = Gaussian(atoms=self.atoms, **self.parameters)
        return calc

    @staticmethod
    def parse_gaussian_input(fd):
        '''Reads a gaussian input file into an atoms object and
        parameters dictionary.

        Parameters
        ----------
        fd: file-like
            Contains the contents of a  gaussian input file

        Returns
        ---------
        GaussianConfiguration
            Contains an atoms object created using the structural
            information from the input file.
            Contains a parameters dictionary, which stores any
            keywords and options found in the link-0 and route
            sections of the input file.
        '''
        # The parameters dict to attach to the calculator
        parameters = {}

        # These variables indicate which section of the
        # input file is currently being read:
        route_section = False
        atoms_section = False
        atoms_saved = False
        readiso = False
        count_iso = 0

        # These will contain info that will be attached to the Atoms object:
        nuclei_props = {key: [] for key in _nuclei_prop_array_names}
        symbols = []
        positions = []
        pbc = np.zeros(3, dtype=bool)
        cell = np.zeros((3, 3))
        npbc = 0

        # Info relating to the z-matrix definition (if set)
        zmatrix_type = False
        zmatrix_contents = ""
        zmatrix_var_section = False
        zmatrix_vars = ""

        # Will store the basis set definition (if set)
        basis_set = ""

        for line in fd:
            link0_match = _re_link0.match(line)
            output_type_match = _re_output_type.match(line)
            chgmult_match = _re_chgmult.match(line)
            # The first blank line appears at the end of the route section
            # and a blank line appears at the end of the atoms section
            if line == '\n' and not readiso:
                route_section = False
                atoms_section = False
            elif link0_match:
                _save_link0_param(
                    link0_match, parameters)
            elif output_type_match and not route_section:
                route_section = True
                # remove #_ ready for looking for method/basis/parameters:
                line = line.strip(output_type_match.group(0))
                parameters.update({'output_type': output_type_match.group(1)})
            elif chgmult_match:
                chgmult = chgmult_match.group(0).split()
                parameters.update(
                    {'charge': int(chgmult[0]), 'mult': int(chgmult[1])})
                # After the charge and multiplicty have been set, the
                # molecule specification section of the input file begins:
                atoms_section = True
            elif atoms_section:
                line = line.split('!')[0].replace('/', ' ')
                line = line.replace(',', ' ')
                if (line.split()):
                    if zmatrix_type:
                        # Save any variables set when defining the z-matrix:
                        if zmatrix_var_section:
                            zmatrix_vars += line.strip() + '\n'
                            continue
                        elif 'variables' in line.lower():
                            zmatrix_var_section = True
                            continue
                        elif 'constants' in line.lower():
                            warnings.warn("Constants in the optimisation are "
                                          "not currently supported. Instead "
                                          "setting constants as variables.")
                            continue

                    line, info = _save_nuclei_props(
                        line, nuclei_props)

                    symbol = info[0]
                    tokens = info[1]

                    if not zmatrix_type:
                        pos = list(tokens[1:])
                        if len(pos) < 3 or (pos[0] == '0' and symbol != 'TV'):
                            zmatrix_type = True
                            zmatrix_contents += line.strip() + '\n'
                        elif len(pos) > 3:
                            raise IOError("ERROR: Gaussian input file could "
                                          "not be read as freeze codes are not"
                                          " supported. If using cartesian "
                                          "coordinates, these must be "
                                          "given as 3 numbers separated "
                                          "by whitespace.")
                        else:
                            try:
                                pos = list(map(float, pos))
                            except ValueError:
                                raise(IOError(
                                    "ERROR: Molecule specification in"
                                    "Gaussian input file could not be read"))
                        if symbol.upper() == 'TV':
                            pbc[npbc] = True
                            cell[npbc] = pos
                            npbc += 1
                        else:
                            if not zmatrix_type:
                                symbols.append(symbol)
                                positions.append(pos)
                    else:
                        line_list = line.split()
                        if len(line_list) == 8 and line_list[7] == '1':
                            raise IOError(
                                "ERROR: Could not read the Gaussian input file"
                                ", as the alternative Z-matrix format using "
                                "two bond angles instead of a bond angle and "
                                "a dihedral angle is not supported.")
                        zmatrix_contents += line.strip() + '\n'

                    atoms_saved = True
            elif atoms_saved:
                # Now that we are past the molecule spec. section, we can read
                # the entire z-matrix (if set):
                if len(positions) == 0:
                    if zmatrix_type:
                        positions, symbols = _read_zmatrix(
                            zmatrix_contents, zmatrix_vars)

                if line.split():
                    # check that the line isn't just a comment
                    if line.split()[0] == '!':
                        continue
                    line = line.strip().split('!')[0]

                readiso = _get_readiso_param(parameters)

                if len(line) > 0 and line[0] == '@':
                    # If the name of a basis file is specified, this line
                    # begins with a '@'
                    parameters['basisfile'] = line
                elif readiso and count_iso < len(symbols) + 1:
                    # The ReadIso section has 1 more line than the number of
                    # symbols
                    if count_iso == 0:
                        # The first line in the readiso section contains the
                        # temperature, pressure, scale. Here we save this:
                        _save_readiso_info(line, parameters)
                        # If the atom masses were set in the nuclei properties
                        # section, they will be overwritten by the ReadIso
                        # section
                        parameters['iso'] = []

                    else:
                        # The remaining lines in the ReadIso section are
                        # the masses of the atoms
                        try:
                            parameters['iso'].append(float(line))
                        except ValueError:
                            parameters['iso'].append(None)
                    count_iso += 1
                else:
                    # If the rest of the file is not the ReadIso section,
                    # then it must be the definition of the basis set.
                    if parameters.get('basis', '').lower() == 'gen' \
                            or 'gen' in parameters.keys():
                        if line.strip() != "":
                            basis_set += line + '\n'

            if route_section:
                _save_route_params(line, parameters)

        _validate_params(parameters)

        if readiso:
            _delete_readiso_param(parameters)
            # Ensures the masses array is the same length as the
            # symbols array:
            if len(parameters['iso']) < len(symbols):
                for i in range(0, len(symbols) - len(parameters['iso'])):
                    parameters['iso'].append(None)
            elif len(parameters['iso']) > len(symbols):
                parameters['iso'] = parameters['iso'][:len(symbols)]

        # Saves the basis set definition to the parameters array if
        # it has been set:
        if basis_set != "":
            parameters['basis_set'] = basis_set
            parameters['basis'] = 'gen'
            parameters.pop('gen', None)

        try:
            atoms = Atoms(symbols, positions, pbc=pbc, cell=cell)
        except (IndexError, ValueError, KeyError) as e:
            raise IOError("ERROR: Could not read the Gaussian input file, "
                          "due to a problem with the molecule specification:"
                          " {}".format(e))

        parameters = _add_nuclei_props_to_params(parameters, nuclei_props)

        return GaussianConfiguration(atoms, parameters)


def read_gaussian_in(fd, get_calculator=False):
    '''
    Reads a gaussian input file and returns an Atoms object.

    Parameters
    ----------
    fd: file-like
        Contains the contents of a  gaussian input file

    get_calculator: bool
        When set to ``True``, a Gaussian calculator will be
        attached to the Atoms object which is returned.
        This will mean that additional information is read
        from the input file (see below for details).

    Returns
    ----------
    Atoms
        An Atoms object containing the following information that has been
        read from the input file: symbols, positions, cell, nuclei properties,
        and masses.
        It is able to read in masses set in the nuclei properties section or
        in the ReadIsotopes section (if ``freq=ReadIso`` is set). Note that if
        a mass has been set to an integer value in the input file, when this is
        read into the Atoms object, it will be kept as an integer, not
        automatically converted to the correct isotopic mass.

    Notes
    ----------
    Gaussian input files can be read where the atoms' locations are set with
    cartesian coordinates or as a z-matrix. Variables may be used in the
    z-matrix definition, but currently setting constants for constraining
    a geometry optimisation is not supported. Note that the `alternative`
    z-matrix definition where two bond angles are set instead of a bond angle
    and a dihedral angle is not currently supported.

    If the parameter ``get_calculator`` is set to ``True``, then the Atoms
    object is returned with a Gaussian calculator attached.

    This Gaussian calculator will contain a parameters dictionary which will
    contain the Link 0 commands and the options and keywords set in the route
    section of the Gaussian input file, as well as:

    • The charge and multiplicity

    • The selected level of output

    • The method, basis set and (optional) fitting basis set.

    • Basis file name/definition if set

    If the Gaussian input file has been generated by ASE's
    ``write_gaussian_in`` method, then the basis set, method and fitting
    basis will be saved under the ``basis``, ``method`` and ``fitting_basis``
    keywords in the parameters dictionary. Otherwise they are saved as
    keywords in the parameters dict.

    Currently this does not support reading of any other sections which would
    be found below the molecule specification that have not been mentioned
    here (such as the ModRedundant section).
    '''
    gaussian_input = GaussianConfiguration.parse_gaussian_input(fd)
    atoms = gaussian_input.get_atoms()

    if get_calculator:
        atoms.calc = gaussian_input.get_calculator()

    return atoms


# In the interest of using the same RE for both atomic positions and forces,
# we make one of the columns optional. That's because atomic positions have
# 6 columns, while forces only has 5 columns. Otherwise they are very similar.
_re_atom = re.compile(
    r'^\s*\S+\s+(\S+)\s+(?:\S+\s+)?(\S+)\s+(\S+)\s+(\S+)\s*$'
)
_re_forceblock = re.compile(r'^\s*Center\s+Atomic\s+Forces\s+\S+\s*$')
_re_l716 = re.compile(r'^\s*\(Enter .+l716.exe\)$')


def _compare_merge_configs(configs, new):
    """Append new to configs if it contains a new geometry or new data.

    Gaussian sometimes repeats a geometry, for example at the end of an
    optimization, or when a user requests vibrational frequency
    analysis in the same calculation as a geometry optimization.

    In those cases, rather than repeating the structure in the list of
    returned structures, try to merge results if doing so doesn't change
    any previously calculated values. If that's not possible, then create
    a new "image" with the new results.
    """
    if not configs:
        configs.append(new)
        return

    old = configs[-1]

    if old != new:
        configs.append(new)
        return

    oldres = old.calc.results
    newres = new.calc.results
    common_keys = set(oldres).intersection(newres)

    for key in common_keys:
        if np.any(oldres[key] != newres[key]):
            configs.append(new)
            return
    else:
        oldres.update(newres)


def read_gaussian_out(fd, index=-1):
    configs = []
    atoms = None
    energy = None
    dipole = None
    forces = None
    for line in fd:
        line = line.strip()
        if line.startswith(r'1\1\GINC'):
            # We've reached the "archive" block at the bottom, stop parsing
            break

        if (line == 'Input orientation:'
                or line == 'Z-Matrix orientation:'):
            if atoms is not None:
                atoms.calc = SinglePointCalculator(
                    atoms, energy=energy, dipole=dipole, forces=forces,
                )
                _compare_merge_configs(configs, atoms)
            atoms = None
            energy = None
            dipole = None
            forces = None

            numbers = []
            positions = []
            pbc = np.zeros(3, dtype=bool)
            cell = np.zeros((3, 3))
            npbc = 0
            # skip 4 irrelevant lines
            for _ in range(4):
                fd.readline()
            while True:
                match = _re_atom.match(fd.readline())
                if match is None:
                    break
                number = int(match.group(1))
                pos = list(map(float, match.group(2, 3, 4)))
                if number == -2:
                    pbc[npbc] = True
                    cell[npbc] = pos
                    npbc += 1
                else:
                    numbers.append(max(number, 0))
                    positions.append(pos)
            atoms = Atoms(numbers, positions, pbc=pbc, cell=cell)
        elif (line.startswith('Energy=')
                or line.startswith('SCF Done:')):
            # Some semi-empirical methods (Huckel, MINDO3, etc.),
            # or SCF methods (HF, DFT, etc.)
            energy = float(line.split('=')[1].split()[0].replace('D', 'e'))
            energy *= Hartree
        elif (line.startswith('E2 =') or line.startswith('E3 =')
                or line.startswith('E4(') or line.startswith('DEMP5 =')
                or line.startswith('E2(')):
            # MP{2,3,4,5} energy
            # also some double hybrid calculations, like B2PLYP
            energy = float(line.split('=')[-1].strip().replace('D', 'e'))
            energy *= Hartree
        elif line.startswith('Wavefunction amplitudes converged. E(Corr)'):
            # "correlated method" energy, e.g. CCSD
            energy = float(line.split('=')[-1].strip().replace('D', 'e'))
            energy *= Hartree
        elif _re_l716.match(line):
            # Sometimes Gaussian will print "Rotating derivatives to
            # standard orientation" after the matched line (which looks like
            # "(Enter /opt/gaussian/g16/l716.exe)", though the exact path
            # depends on where Gaussian is installed). We *skip* the dipole
            # in this case, because it might be rotated relative to the input
            # orientation (and also it is numerically different even if the
            # standard orientation is the same as the input orientation).
            line = fd.readline().strip()
            if not line.startswith('Dipole'):
                continue
            dip = line.split('=')[1].replace('D', 'e')
            tokens = dip.split()
            dipole = []
            # dipole elements can run together, depending on what method was
            # used to calculate them. First see if there is a space between
            # values.
            if len(tokens) == 3:
                dipole = list(map(float, tokens))
            elif len(dip) % 3 == 0:
                # next, check if the number of tokens is divisible by 3
                nchars = len(dip) // 3
                for i in range(3):
                    dipole.append(float(dip[nchars * i:nchars * (i + 1)]))
            else:
                # otherwise, just give up on trying to parse it.
                dipole = None
                continue
            # this dipole moment is printed in atomic units, e-Bohr
            # ASE uses e-Angstrom for dipole moments.
            dipole = np.array(dipole) * Bohr
        elif _re_forceblock.match(line):
            # skip 2 irrelevant lines
            fd.readline()
            fd.readline()
            forces = []
            while True:
                match = _re_atom.match(fd.readline())
                if match is None:
                    break
                forces.append(list(map(float, match.group(2, 3, 4))))
            forces = np.array(forces) * Hartree / Bohr
    if atoms is not None:
        atoms.calc = SinglePointCalculator(
            atoms, energy=energy, dipole=dipole, forces=forces,
        )
        _compare_merge_configs(configs, atoms)
    return configs[index]

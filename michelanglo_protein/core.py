import pickle, os, re, json
from datetime import datetime
from .settings_handler import global_settings #the instance not the class.
from collections import namedtuple
import gzip, requests
from michelanglo_transpiler import PyMolTranspiler

from warnings import warn
from .metadata_from_PDBe import PDBMeta
from typing import Dict

Variant = namedtuple('Variant', ['id', 'x', 'y', 'impact', 'description', 'homozygous'], defaults=(None, None, None, None, None, None))
Variant.__doc__="""
Stores the gnomAD data for easy use by FeatureViewer and co. Can be converted to Mutation.
"""

class Structure:
    #lolz. a C++ coder would hate this name. Sturcture as in "protein structure"
    #that is not funny. Why I did I think it was?
    #Why am I talking to my past self?!
    """
    No longer a namedtuple.
    Stores the structural data for easy use by FeatureViewer and co. Can be converted to StructureAnalyser
    type = rcsb | swissmodel | homologue
    """
    settings = global_settings

    #__slots__ = ['id', 'description', 'x', 'y', 'url','type','chain','offset', 'coordinates', 'extra']
    def __init__(self, id, description, x:int, y:int, code, type='rcsb',chain='*',offset:int=0, coordinates=None, extra=None, url=''):
        """
        Stores the structural data for easy use by FeatureViewer and co. Can be converted to StructureAnalyser
        type = rcsb | swissmodel | homologue | www | local
        """
        self.id = id #: RCSB code
        self.description = description #: description
        self.x = int(x)  #: resi in the whole uniprot protein
        self.y = int(y)  #: end resi in the whole uniprot protein
        self.offset = int(offset) #: offset is the number *subtracted* from the PDB index to make it match the position in Uniprot.
        self.offsets = {} if chain == '*' else {chain: int(offset)} ### this is going to be the only one.
        self.pdb_start = None  # no longer used. TO be deleted.
        self.pdb_end = None   # ditto.
        self.resolution = 0 #: crystal resolution. 0 or lower will trigger special cases
        self.code = code
        self.chain_definitions = [] #filled by SIFT. This is a list with a Dict per chain.
        self.type = type.lower() #: str: rcsb | swissmodel | homologue | www | local
        self.chain = chain #: type str: chain letter or * (all)
        if extra is None:
            self.extra = {}
        else:
            self.extra = extra
        self.coordinates = coordinates #: PDBblock
        self.url = url  ## for type = www or local or swissmodel
        # https://files.rcsb.org/download/{self.code}.pdb does not work (often) while the url is something odd.

    def to_dict(self) -> Dict:
        return {'x': self.x, 'y': self.y, 'id': self.id, 'description': self.description}

    def __str__(self):
        return str(self.to_dict())

    def get_coordinates(self) -> str:
        """
        Gets the coordinates (PDB block) based on ``self.url`` and ``self.type``
        :return: coordinates
        :rtype: str
        """
        if self.type == 'rcsb':
            r = requests.get(f'https://files.rcsb.org/download/{self.code}.pdb')
        elif self.type == 'swissmodel':
            r = requests.get(self.url)
        elif self.type == 'www':
            r = requests.get(self.url)
        elif self.type == 'local':
            self.coordinates = open(self.url).read()
            return self.coordinates
        else:
            warn(f'Model type {self.type}  for {self.id} could not be recognised.')
            return None
        if r.status_code == 200:
            self.coordinates = r.text
        else:
            warn(f'Model {self.code} failed.')
        return self.coordinates

    def get_offset_coordinates(self):
        """
        Gets the coordinates and offsets them.
        :return:
        """
        if not self.chain_definitions:
            self.lookup_sifts()
        self.coordinates = PyMolTranspiler.renumber(self.get_coordinates(), self.chain_definitions, 'str', make_A=self.chain).raw_pdb
        return self.coordinates

    def includes(self, position, offset=0):
        """
        Generally there should not be an offset as x and y are from Uniprot data so they are already fixed!
        :param position:
        :param offset:
        :return:
        """
        if self.x + offset > position:
            return False
        elif self.y + offset < position:
            return False
        else:
            return True


    def lookup_sifts(self):
        """
        SIFTS data. for PDBe query see elsewhere.
        There are four start/stop pairs that need to be compared to get a good idea of a protein.
        For a lengthy discussion see https://blog.matteoferla.com/2019/09/pdb-numbering-rollercoaster.html
        Also for a good list of corner case models see https://proteopedia.org/wiki/index.php/Unusual_sequence_numbering
        :return: self
        """
        def get_offset(detail):
            if detail['PDB_BEG'] == 'None':
                # assuming 1 is the start, which is pretty likely.
                b = int(detail['RES_BEG'])
                if b != 1:
                    warn('SP_BEG is not 1, yet PDB_BEG is without a crystallised start')
            else:
                r = re.search('(-?\d+)', detail['PDB_BEG'])
                if r is None:
                    return self
                b = int(r.group(1))
            return int(detail['SP_BEG']) - b

        if self.type != 'rcsb':
            return self
        details = self._get_sifts()
        ## get matching chain.
        self.chain_definitions = [{'chain': d['CHAIN'],
                                   'uniprot': d['SP_PRIMARY'],
                                   'x': int(d["SP_BEG"]),
                                   'y': int(d["SP_END"]),
                                   'offset': get_offset(d),
                                   'range': f'{d["SP_BEG"]}-{d["SP_END"]}',
                                   'name': None,
                                   'description': None} for d in details]
        try:
            if self.chain != '*':
                detail = next(filter(lambda x: self.chain == x['CHAIN'], details))
                self.offset = get_offset(detail)
        except StopIteration:
            warn(f'{self.code} {self.chain} not in {details}')
            return self
        self.offsets = {d['chain']: d['offset'] for d in self.chain_definitions}
        return self

    def _get_sifts(self, all_chains=True): #formerly called .lookup_pdb_chain_uniprot
        details = []
        headers = 'PDB     CHAIN   SP_PRIMARY      RES_BEG RES_END PDB_BEG PDB_END SP_BEG  SP_END'.split()
        with self.settings.open('pdb_chain_uniprot') as fh:
            for row in fh:
                if self.code.lower() == row[0:4]:
                    entry = dict(zip(headers, row.split()))
                    if self.chain == entry['CHAIN'] or all_chains:
                        details.append(entry)
        return details

    def lookup_resolution(self):
        if self.type != 'rcsb':
            return self
        with self.settings.open('resolution') as fh:
            resolution = json.load(fh)
            for entry in resolution:
                if entry['IDCODE'] == self.code:
                    if entry['RESOLUTION'].strip():
                        self.resolution = float(entry['RESOLUTION'])
                    break
            else:
                warn(f'No resolution info for {self.code}')
        return self

    def lookup_ligand(self):
        warn('TEMP! Returns the data... not self')
        return PDBMeta(self.code+'_'+self.chain).data


class ProteinCore:
    """
    This is a lightweight version of Protein that is intended to run off pre parsed pickles.
    It forms the base of Protein. This does zero protein analyses.
    It has IO powers though .dump/.gdump saves an instance .load/.gload loads and can work as a class method if the filename is provided as an argument.
    The gzipped forms (.gdump and .gload) are about 1/3 the size. 50 KB.

    The content of a protein looks like
    ENSG ENSG00000078369
    ENSP
    ENST ENST00000610897
    ExAC_type Unknown
    accession_list ['P62873', 'B1AJZ7', 'P04697', 'P04901', 'Q1RMY8']
    alt_gene_name_list []
    alternative_fullname_list []
    alternative_shortname_list []
    diseases [{'id': 'DI-04731', 'description': 'A form of mental retardation, a disorder characterized by significantly below average general intellectual functioning associated with impairments in adaptive behavior and manifested during the developmental period. MRD42 patients manifest global developmental delay commonly accompanied by hypotonia, seizures of various types, ophthalmological manifestations, and poor growth.', 'name': 'Mental retardation, autosomal dominant 42', 'MIM': '616973'}]
    features {'initiator methionine': [{'x': 1, 'y': 1, 'description': 'Removed', 'id': 'initiatormethionine_1', 'type': 'initiator methionine'}], 'chain': [{'x': 2, 'y': 340, 'description': 'Guanine nucleotide-binding protein G(I)/G(S)/G(T) subunit beta-1', 'id': 'chain_2_340', 'type': 'chain'}], 'repeat': [{'x': 53, 'y': 83, 'description': 'WD 1', 'id': 'repeat_53_83', 'type': 'repeat'}, {'x': 95, 'y': 125, 'description': 'WD 2', 'id': 'repeat_95_125', 'type': 'repeat'}, {'x': 141, 'y': 170, 'description': 'WD 3', 'id': 'repeat_141_170', 'type': 'repeat'}, {'x': 182, 'y': 212, 'description': 'WD 4', 'id': 'repeat_182_212', 'type': 'repeat'}, {'x': 224, 'y': 254, 'description': 'WD 5', 'id': 'repeat_224_254', 'type': 'repeat'}, {'x': 268, 'y': 298, 'description': 'WD 6', 'id': 'repeat_268_298', 'type': 'repeat'}, {'x': 310, 'y': 340, 'description': 'WD 7', 'id': 'repeat_310_340', 'type': 'repeat'}], 'modified residue': [{'x': 2, 'y': 2, 'description': 'N-acetylserine', 'id': 'modifiedresidue_2', 'type': 'modified residue'}, {'x': 2, 'y': 2, 'description': 'Phosphoserine', 'id': 'modifiedresidue_2', 'type': 'modified residue'}, {'x': 266, 'y': 266, 'description': 'Phosphohistidine', 'id': 'modifiedresidue_266', 'type': 'modified residue'}], 'splice variant': [{'x': 329, 'y': 340, 'description': 'In isoform 2.', 'id': 'splicevariant_329_340', 'type': 'splice variant'}], 'sequence variant': [{'x': 30, 'y': 30, 'description': 'In MRD42; unknown pathological significance; no effect on protein abundance; no effect on complex formation with gamma subunit; no effect on trimer formation with alpha and gamma subunits; no effect on receptor-driven G protein activation; dbSNP:rs764997309.', 'id': 'sequencevariant_30', 'type': 'sequence variant'}, {'x': 52, 'y': 52, 'description': 'In MRD42; decreases receptor-driven G protein activation; no effect on protein abundance; no effect on complex formation with gamma subunit; decreases trimer formation with alpha and gamma subunit.', 'id': 'sequencevariant_52', 'type': 'sequence variant'}, {'x': 64, 'y': 64, 'description': 'In MRD42; decreases receptor-driven G protein activation; decreases protein abundance; decreases complex formation with gamma subunit; decreases trimer formation with alpha and gamma subunit.', 'id': 'sequencevariant_64', 'type': 'sequence variant'}, {'x': 76, 'y': 76, 'description': 'In MRD42; dbSNP:rs869312822.', 'id': 'sequencevariant_76', 'type': 'sequence variant'}, {'x': 76, 'y': 76, 'description': 'In MRD42; dbSNP:rs869312821.', 'id': 'sequencevariant_76', 'type': 'sequence variant'}, {'x': 77, 'y': 77, 'description': 'In MRD42; dbSNP:rs758432471.', 'id': 'sequencevariant_77', 'type': 'sequence variant'}, {'x': 78, 'y': 78, 'description': 'In MRD42; dbSNP:rs869312823.', 'id': 'sequencevariant_78', 'type': 'sequence variant'}, {'x': 80, 'y': 80, 'description': 'In MRD42; also found in patients with acute lymphoblastic T-cell leukemia; reduces interaction with GNAI2, GNAI3, GNA13 and GNA11; induces activation of PI3K-AKT-mTOR and MAPK pathways; dbSNP:rs752746786.', 'id': 'sequencevariant_80', 'type': 'sequence variant'}, {'x': 80, 'y': 80, 'description': 'In MRD42; also found in patient with hematologic malignancies; reduces interaction with GNAI2, GNAI3, GNA13 and GNA11; induces activation of PI3K-AKT-mTOR and MAPK pathways; dbSNP:rs752746786.', 'id': 'sequencevariant_80', 'type': 'sequence variant'}, {'x': 91, 'y': 91, 'description': 'In MRD42; unknown pathological significance; no effect on protein abundance; no effect on complex formation with gamma subunit; no effect on trimer formation with apha and gamma subunits; no effect on receptor-driven G protein activation.', 'id': 'sequencevariant_91', 'type': 'sequence variant'}, {'x': 92, 'y': 92, 'description': 'In MRD42; decreases receptor-driven G protein activation; increases trimer formation with alpha and gamma subunits; no effect on protein abundance; no effect on complex formation with gamma subunit.', 'id': 'sequencevariant_92', 'type': 'sequence variant'}, {'x': 94, 'y': 94, 'description': 'In MRD42; decreases receptor-driven G protein activation; decreases trimer formation with alpha and gamma subunit; no effect on protein abundance;no effect on complex formation with gamma subunit.', 'id': 'sequencevariant_94', 'type': 'sequence variant'}, {'x': 95, 'y': 95, 'description': 'In MRD42; dbSNP:rs869312824.', 'id': 'sequencevariant_95', 'type': 'sequence variant'}, {'x': 96, 'y': 96, 'description': 'In MRD42; decreases receptor-driven G protein activation; decreases trimer formation with alpha and gamma subunit; no effect on protein abundance; no effect on complex formation with gamma subunit.', 'id': 'sequencevariant_96', 'type': 'sequence variant'}, {'x': 101, 'y': 101, 'description': 'In MRD42; dbSNP:rs869312825.', 'id': 'sequencevariant_101', 'type': 'sequence variant'}, {'x': 106, 'y': 106, 'description': 'In MRD42; decreases receptor-driven G protein activation; decreases complex formation with gamma subunit; decreases trimer formation with alpha and gamma subunit; no effect on protein abundance.', 'id': 'sequencevariant_106', 'type': 'sequence variant'}, {'x': 118, 'y': 118, 'description': 'In MRD42; decreases receptor-driven G protein activation; no effect on protein abundance; no effect on complex formation with gamma subunit; no effect on trimer formation with alpha and gamma subunits; dbSNP:rs1553194162.', 'id': 'sequencevariant_118', 'type': 'sequence variant'}, {'x': 326, 'y': 326, 'description': 'In MRD42; dbSNP:rs869312826.', 'id': 'sequencevariant_326', 'type': 'sequence variant'}, {'x': 337, 'y': 337, 'description': 'In MRD42; unknown pathological significance; no effect on protein abundance; no effect on complex formation with gamma subunit; no effect on trimer formation with alpha and gamma subunits; no effect on receptor-driven G protein activation.', 'id': 'sequencevariant_337', 'type': 'sequence variant'}], 'helix': [{'x': 3, 'y': 25, 'description': 'helix', 'id': 'helix_3_25', 'type': 'helix'}, {'x': 30, 'y': 33, 'description': 'helix', 'id': 'helix_30_33', 'type': 'helix'}], 'turn': [{'x': 34, 'y': 36, 'description': 'turn', 'id': 'turn_34_36', 'type': 'turn'}, {'x': 75, 'y': 77, 'description': 'turn', 'id': 'turn_75_77', 'type': 'turn'}, {'x': 84, 'y': 86, 'description': 'turn', 'id': 'turn_84_86', 'type': 'turn'}, {'x': 162, 'y': 164, 'description': 'turn', 'id': 'turn_162_164', 'type': 'turn'}, {'x': 171, 'y': 174, 'description': 'turn', 'id': 'turn_171_174', 'type': 'turn'}, {'x': 213, 'y': 216, 'description': 'turn', 'id': 'turn_213_216', 'type': 'turn'}, {'x': 246, 'y': 248, 'description': 'turn', 'id': 'turn_246_248', 'type': 'turn'}, {'x': 255, 'y': 258, 'description': 'turn', 'id': 'turn_255_258', 'type': 'turn'}, {'x': 299, 'y': 301, 'description': 'turn', 'id': 'turn_299_301', 'type': 'turn'}], 'strand': [{'x': 47, 'y': 51, 'description': 'strand', 'id': 'strand_47_51', 'type': 'strand'}, {'x': 58, 'y': 63, 'description': 'strand', 'id': 'strand_58_63', 'type': 'strand'}, {'x': 67, 'y': 74, 'description': 'strand', 'id': 'strand_67_74', 'type': 'strand'}, {'x': 78, 'y': 83, 'description': 'strand', 'id': 'strand_78_83', 'type': 'strand'}, {'x': 89, 'y': 94, 'description': 'strand', 'id': 'strand_89_94', 'type': 'strand'}, {'x': 96, 'y': 98, 'description': 'strand', 'id': 'strand_96_98', 'type': 'strand'}, {'x': 100, 'y': 105, 'description': 'strand', 'id': 'strand_100_105', 'type': 'strand'}, {'x': 109, 'y': 116, 'description': 'strand', 'id': 'strand_109_116', 'type': 'strand'}, {'x': 117, 'y': 119, 'description': 'strand', 'id': 'strand_117_119', 'type': 'strand'}, {'x': 120, 'y': 127, 'description': 'strand', 'id': 'strand_120_127', 'type': 'strand'}, {'x': 129, 'y': 131, 'description': 'strand', 'id': 'strand_129_131', 'type': 'strand'}, {'x': 134, 'y': 140, 'description': 'strand', 'id': 'strand_134_140', 'type': 'strand'}, {'x': 146, 'y': 153, 'description': 'strand', 'id': 'strand_146_153', 'type': 'strand'}, {'x': 156, 'y': 161, 'description': 'strand', 'id': 'strand_156_161', 'type': 'strand'}, {'x': 166, 'y': 170, 'description': 'strand', 'id': 'strand_166_170', 'type': 'strand'}, {'x': 175, 'y': 180, 'description': 'strand', 'id': 'strand_175_180', 'type': 'strand'}, {'x': 187, 'y': 192, 'description': 'strand', 'id': 'strand_187_192', 'type': 'strand'}, {'x': 196, 'y': 203, 'description': 'strand', 'id': 'strand_196_203', 'type': 'strand'}, {'x': 208, 'y': 212, 'description': 'strand', 'id': 'strand_208_212', 'type': 'strand'}, {'x': 217, 'y': 222, 'description': 'strand', 'id': 'strand_217_222', 'type': 'strand'}, {'x': 229, 'y': 234, 'description': 'strand', 'id': 'strand_229_234', 'type': 'strand'}, {'x': 238, 'y': 245, 'description': 'strand', 'id': 'strand_238_245', 'type': 'strand'}, {'x': 250, 'y': 254, 'description': 'strand', 'id': 'strand_250_254', 'type': 'strand'}, {'x': 259, 'y': 264, 'description': 'strand', 'id': 'strand_259_264', 'type': 'strand'}, {'x': 273, 'y': 278, 'description': 'strand', 'id': 'strand_273_278', 'type': 'strand'}, {'x': 280, 'y': 282, 'description': 'strand', 'id': 'strand_280_282', 'type': 'strand'}, {'x': 284, 'y': 289, 'description': 'strand', 'id': 'strand_284_289', 'type': 'strand'}, {'x': 292, 'y': 298, 'description': 'strand', 'id': 'strand_292_298', 'type': 'strand'}, {'x': 304, 'y': 309, 'description': 'strand', 'id': 'strand_304_309', 'type': 'strand'}, {'x': 315, 'y': 320, 'description': 'strand', 'id': 'strand_315_320', 'type': 'strand'}, {'x': 322, 'y': 325, 'description': 'strand', 'id': 'strand_322_325', 'type': 'strand'}, {'x': 327, 'y': 331, 'description': 'strand', 'id': 'strand_327_331', 'type': 'strand'}, {'x': 336, 'y': 339, 'description': 'strand', 'id': 'strand_336_339', 'type': 'strand'}], 'PSP_modified_residues': [{'symbol': 'GNB1', 'residue_index': 19, 'from_residue': 'R', 'ptm': 'm1', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 22, 'from_residue': 'R', 'ptm': 'm1', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 15, 'from_residue': 'K', 'ptm': 'ub', 'count': 3}, {'symbol': 'GNB1', 'residue_index': 23, 'from_residue': 'K', 'ptm': 'ub', 'count': 35}, {'symbol': 'GNB1', 'residue_index': 57, 'from_residue': 'K', 'ptm': 'ub', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 89, 'from_residue': 'K', 'ptm': 'ub', 'count': 5}, {'symbol': 'GNB1', 'residue_index': 209, 'from_residue': 'K', 'ptm': 'ub', 'count': 8}, {'symbol': 'GNB1', 'residue_index': 280, 'from_residue': 'K', 'ptm': 'ub', 'count': 4}, {'symbol': 'GNB1', 'residue_index': 301, 'from_residue': 'K', 'ptm': 'ub', 'count': 18}, {'symbol': 'GNB1', 'residue_index': 2, 'from_residue': 'S', 'ptm': 'p', 'count': 6}, {'symbol': 'GNB1', 'residue_index': 31, 'from_residue': 'S', 'ptm': 'p', 'count': 2}, {'symbol': 'GNB1', 'residue_index': 50, 'from_residue': 'T', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 67, 'from_residue': 'S', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 72, 'from_residue': 'S', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 74, 'from_residue': 'S', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 85, 'from_residue': 'Y', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 111, 'from_residue': 'Y', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 136, 'from_residue': 'S', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 143, 'from_residue': 'T', 'ptm': 'p', 'count': 1}, {'symbol': 'GNB1', 'residue_index': 266, 'from_residue': 'H', 'ptm': 'p', 'count': 4}]}
    gene_name GNB1
    gnomAD []
    logbook ['[2020-01-02 16:10:11.253535]\tSwissmodel has 1 models.', '[2020-01-07 18:21:50.206847]\tData from the pickled dictionary ../protein-data/pickle/taxid9606/P62873.p', '[2020-01-07 18:21:50.240169]\tGetting PTM for P62873', '[2020-01-08 12:20:25.193700]\tData from the pickled dictionary ../protein-data/pickle/taxid9606/P62873.p']
    organism {'common': 'Human', 'scientific': 'Homo sapiens', 'NCBI Taxonomy': '9606', 'other': 'NA'}
    other {}
    pLI -1
    pNull -1
    pRec -1
    partners {'interactant': ['G proteins are composed of 3 units, alpha, beta and gamma. Interacts with ARHGEF18 and RASD2 (PubMed:14512443, PubMed:19255495). The heterodimer formed by GNB1 and GNG2 interacts with ARHGEF5 (PubMed:19713215).', 'GNG2', 'NCF2'], 'BioGRID': [], 'SSL': [], 'HuRI': [], 'stringDB highest': [], 'stringDB high': [], 'stringDB medium': [], 'stringDB low': []}
    pdb_matches []
    pdbs [<michelanglo_protein.core.Structure object at 0x7f654b6f7dd8>, <michelanglo_protein.core.Structure object at 0x7f6551370e48>, <michelanglo_protein.core.Structure object at 0x7f654b6894a8>, <michelanglo_protein.core.Structure object at 0x7f654b6896d8>, <michelanglo_protein.core.Structure object at 0x7f654b689908>, <michelanglo_protein.core.Structure object at 0x7f654b689b38>, <michelanglo_protein.core.Structure object at 0x7f654b689d68>, <michelanglo_protein.core.Structure object at 0x7f654b689f98>, <michelanglo_protein.core.Structure object at 0x7f654b68e208>, <michelanglo_protein.core.Structure object at 0x7f654b68e438>, <michelanglo_protein.core.Structure object at 0x7f654b68e710>, <michelanglo_protein.core.Structure object at 0x7f654b68e9e8>, <michelanglo_protein.core.Structure object at 0x7f654b68ec18>, <michelanglo_protein.core.Structure object at 0x7f654b68ef28>, <michelanglo_protein.core.Structure object at 0x7f654b692278>, <michelanglo_protein.core.Structure object at 0x7f654b692588>, <michelanglo_protein.core.Structure object at 0x7f654b692908>, <michelanglo_protein.core.Structure object at 0x7f654b692f28>, <michelanglo_protein.core.Structure object at 0x7f654b697278>, <michelanglo_protein.core.Structure object at 0x7f654b6975f8>, <michelanglo_protein.core.Structure object at 0x7f654b697d68>, <michelanglo_protein.core.Structure object at 0x7f654b69b0b8>, <michelanglo_protein.core.Structure object at 0x7f654b69b3c8>, <michelanglo_protein.core.Structure object at 0x7f654b69b710>, <michelanglo_protein.core.Structure object at 0x7f654b69b9e8>, <michelanglo_protein.core.Structure object at 0x7f654b69bcc0>, <michelanglo_protein.core.Structure object at 0x7f654b69bf98>]
    percent_modelled -1
    properties {'kd': [-1.0983333333333334, -1.5508333333333333, -1.4024999999999999, -1.6600000000000001, -2.171666666666667, -1.7100000000000002, -1.5791666666666664, -2.0008333333333335, -1.894166666666667, -1.3208333333333333, -1.5125, -1.8633333333333335, -1.5075000000000003, -1.4391666666666663, -2.06, -1.7575, -1.3024999999999998, -1.0091666666666665, -1.22, -0.4825000000000001, -0.11083333333333327, 0.10999999999999992, 0.3366666666666666, 0.4008333333333332, 0.48583333333333334, 0.3875, -0.013333333333333383, -0.19583333333333353, -0.06583333333333341, -0.43249999999999994, -0.9300000000000002, -0.6191666666666666, -0.24249999999999985, -0.795, -0.3558333333333334, -0.15833333333333335, 0.014166666666666642, -0.5933333333333334, -0.38916666666666666, -0.8475, -1.5250000000000001, -1.86, -1.5266666666666664, -2.1041666666666665, -1.8500000000000003, -1.8799999999999997, -1.1741666666666666, -0.9674999999999999, -0.7300000000000001, -0.12250000000000005, 0.09999999999999994, 0.10166666666666668, 0.5058333333333331, 0.5291666666666666, 0.5074999999999998, 0.19000000000000003, -0.06916666666666675, -0.34500000000000003, -0.8158333333333334, -1.1541666666666668, -1.1933333333333334, -1.0616666666666668, -0.37333333333333335, -0.04916666666666666, 0.61, 0.9533333333333333, 1.0058333333333334, 0.5608333333333332, 0.4091666666666666, -0.5341666666666666, -0.9266666666666667, -0.9241666666666667, -0.32083333333333336, -0.07333333333333332, 0.3266666666666665, 0.7216666666666666, 0.7508333333333331, 0.3458333333333332, 0.10416666666666667, -0.585, -1.2825, -1.36, -1.4533333333333331, -1.1725, -0.6916666666666665, -0.3091666666666666, 0.12583333333333316, 0.24499999999999988, 0.5075, 0.45416666666666655, 0.019166666666666627, 0.12500000000000014, 0.1191666666666666, -0.040000000000000036, 0.40333333333333327, 0.6425000000000001, 0.9508333333333333, 1.0716666666666668, 1.0258333333333336, 0.8525, 0.36083333333333334, -0.08166666666666667, -0.39916666666666667, -0.4533333333333333, -0.4308333333333332, -0.12250000000000005, 0.06833333333333325, 0.5008333333333334, 0.9199999999999999, 0.8208333333333333, 0.6283333333333333, 0.7225000000000001, 0.5541666666666668, 0.24333333333333332, 0.4566666666666667, 0.7541666666666665, 0.6374999999999998, 0.6099999999999999, 0.6033333333333333, 0.3999999999999999, -0.43, -0.935, -1.3108333333333333, -1.9491666666666667, -1.7725000000000002, -1.7866666666666664, -1.55, -1.1058333333333332, -0.9841666666666667, -0.9775000000000001, -0.5008333333333334, -0.4891666666666666, -0.4200000000000001, -0.7308333333333333, -0.3125, -0.5566666666666665, -0.6475000000000002, -0.2766666666666668, -0.11166666666666662, -0.10333333333333333, 0.1658333333333333, 0.23249999999999993, 0.59, 0.9399999999999998, 0.7433333333333333, 0.36166666666666664, -0.12750000000000003, -0.5575, -0.8891666666666668, -0.8908333333333333, -0.585, -0.545, -0.32166666666666677, 0.08249999999999987, -0.027500000000000118, -0.09083333333333336, -0.21916666666666654, -0.5325, -0.6849999999999999, -0.17500000000000007, 0.13833333333333334, 0.27083333333333326, 0.6875, 0.7124999999999999, 0.44249999999999995, 0.20249999999999999, -0.22999999999999998, -0.8833333333333334, -1.3008333333333335, -1.4100000000000001, -1.4308333333333334, -1.4816666666666667, -1.075, -0.8350000000000001, -0.7866666666666667, -0.49750000000000005, -0.4333333333333333, -0.7550000000000002, -0.6483333333333333, -0.4474999999999998, -0.5075000000000001, -0.045000000000000075, 0.3508333333333333, 0.93, 1.1916666666666667, 1.2816666666666665, 1.1341666666666668, 0.7174999999999999, 0.04083333333333335, -0.12500000000000003, -0.3941666666666667, -0.03833333333333322, -0.08749999999999991, 0.27333333333333326, 0.7675, 1.2525, 0.9858333333333333, 1.1658333333333333, 0.7791666666666668, 0.5924999999999999, -0.04249999999999998, 0.2266666666666666, 0.15083333333333335, -0.18250000000000008, -0.1641666666666667, -0.11333333333333329, -0.6066666666666667, -0.7825000000000001, -0.7683333333333331, -0.5941666666666666, -1.0175, -1.0208333333333335, -0.8191666666666665, -0.9050000000000001, -0.8116666666666666, -0.645, -0.7166666666666667, -0.9591666666666666, -1.2266666666666666, -1.3050000000000002, -1.0716666666666665, -1.3033333333333335, -1.2575, -0.7308333333333333, -0.08000000000000007, 0.5349999999999998, 1.3275, 1.6066666666666667, 1.5866666666666667, 1.14, 0.7824999999999999, 0.24249999999999985, -0.17333333333333334, -0.21499999999999994, -0.30833333333333335, -0.1541666666666668, 0.1266666666666667, 0.10666666666666662, -0.30750000000000005, -0.3016666666666666, -0.7058333333333332, -0.8708333333333332, -1.1533333333333333, -0.6708333333333334, -0.2799999999999999, -0.1975000000000001, 0.25166666666666665, 0.38000000000000006, 0.31749999999999995, -0.044166666666666764, -0.46916666666666673, -0.8175, -1.0000000000000002, -1.1675, -0.9533333333333335, -1.0133333333333334, -0.4566666666666667, -0.6450000000000002, -0.7625000000000003, -1.02, -0.8541666666666665, -0.9274999999999999, -0.6875, -0.24750000000000005, 0.6916666666666665, 1.1183333333333332, 1.3758333333333332, 1.7541666666666667, 1.8366666666666667, 1.4933333333333334, 1.1275, 0.7708333333333334, 0.4966666666666666, 0.05499999999999994, -0.4808333333333333, -0.5491666666666667, -0.5558333333333333, -0.11416666666666685, 0.13916666666666652, 0.7358333333333333, 1.1816666666666666, 1.0316666666666665, 0.5674999999999999, 0.5066666666666666, -0.34249999999999997, -0.7191666666666667, -1.1633333333333333, -0.8283333333333335, -0.7316666666666666, -0.6283333333333335, -0.3249999999999999, 0.24416666666666664, -0.165, 0.08583333333333328, -0.13416666666666657, -0.28583333333333333, -0.7283333333333332, -0.7600000000000001, -0.49916666666666654, -0.2933333333333333, 0.060833333333333406, 0.6391666666666665, 0.7058333333333332, 0.7283333333333332, 0.42749999999999994, -0.49249999999999994, -0.7883333333333332, -1.3541666666666667, -1.4233333333333331, -0.9900000000000001, -0.38000000000000006, 0.42333333333333334, 0.9775, 1.115, 0.9291666666666666, 0.4075, 0.13250000000000003, -0.22416666666666663, -0.19249999999999998, 0.22916666666666688, 0.3475, 0.6824999999999998, 0.9899999999999999, 0.9733333333333332, 0.4541666666666666, -0.04333333333333328, -0.24916666666666665, -0.33333333333333326, -0.5858333333333334, -0.051666666666666715, 0.24499999999999997, 0.21249999999999983], 'Flex': [1.0149666666666668, 1.0212833333333333, 1.01565, 1.01935, 1.030175, 1.0261083333333334, 1.0298416666666668, 1.0385000000000002, 1.0382583333333335, 1.0287500000000003, 1.0246166666666667, 1.0265166666666667, 1.0171333333333334, 1.011825, 1.0218083333333334, 1.0174916666666667, 1.0089750000000002, 1.0057666666666667, 1.0128666666666668, 1.0057666666666667, 0.9976333333333333, 0.9951916666666668, 0.9979250000000001, 0.9940916666666667, 0.9921166666666666, 0.9959833333333332, 1.000825, 1.0006666666666668, 0.9969916666666666, 1.0035999999999998, 1.0133, 1.0077083333333334, 1.0061833333333332, 1.0130583333333334, 1.0065916666666668, 1.0009833333333333, 0.9952833333333334, 0.9968083333333332, 0.9897, 0.9893916666666667, 0.9935749999999999, 0.9937333333333332, 0.9910166666666665, 0.9952666666666667, 0.9944416666666668, 0.9930583333333334, 0.9860416666666666, 0.984575, 0.9896416666666666, 0.9842416666666668, 0.9803083333333333, 0.9840249999999999, 0.9813416666666667, 0.9714666666666666, 0.965075, 0.9676750000000002, 0.9659166666666668, 0.9659, 0.9784583333333332, 0.992525, 0.9963083333333334, 0.9995499999999998, 0.9963833333333333, 0.997525, 0.9875916666666665, 0.9868333333333336, 0.9868416666666667, 0.9959083333333333, 1.0046416666666667, 1.022925, 1.0287, 1.0290083333333333, 1.017575, 1.0049666666666668, 0.9928083333333335, 0.9856583333333333, 0.9751416666666666, 0.974275, 0.9759500000000001, 0.9870583333333335, 0.999375, 1.0045666666666666, 1.0085833333333334, 1.0045333333333335, 0.9965, 0.9957000000000001, 0.9873666666666666, 0.9847000000000001, 0.9833666666666666, 0.9857833333333333, 0.9871083333333335, 0.9885666666666667, 0.9866333333333334, 0.9861750000000001, 0.9703666666666667, 0.9681833333333333, 0.9607166666666666, 0.9561583333333333, 0.9568333333333333, 0.9703, 0.9811916666666667, 0.9949416666666666, 0.9996833333333331, 1.00405, 1.0023250000000001, 0.9953416666666666, 0.9878750000000002, 0.9816250000000001, 0.9745499999999999, 0.9772916666666666, 0.9851666666666666, 0.9907833333333333, 0.9897666666666668, 0.99505, 0.993, 0.9793, 0.9761833333333333, 0.9762083333333335, 0.9762749999999999, 0.978075, 0.990325, 1.0051583333333334, 1.0132083333333333, 1.024175, 1.0267833333333334, 1.0256666666666667, 1.0208249999999999, 1.0117916666666666, 1.0059, 1.0088583333333334, 0.9997833333333334, 1.0007750000000002, 1.0022666666666666, 1.0048083333333333, 0.9996, 1.0032416666666666, 0.9951416666666666, 0.98685, 0.9834499999999999, 0.9809916666666666, 0.9709666666666666, 0.9665833333333333, 0.9636, 0.9543166666666667, 0.9554833333333334, 0.9680749999999999, 0.9786083333333334, 0.9876083333333335, 1.0004333333333333, 1.0059500000000001, 1.0041416666666667, 1.0071999999999999, 1.0066, 1.0026333333333333, 1.0070583333333334, 1.0111333333333332, 1.0144916666666668, 1.0149166666666667, 1.0146499999999998, 1.0028416666666666, 0.985375, 0.9777416666666666, 0.9688833333333333, 0.9693666666666667, 0.9736416666666666, 0.9848333333333334, 0.9975166666666668, 1.0091750000000002, 1.0160333333333333, 1.022633333333333, 1.018375, 1.0143, 1.0029333333333332, 1.0003, 0.9916999999999999, 0.9856583333333333, 0.9869833333333333, 0.9942916666666667, 0.9925416666666668, 0.9928916666666666, 1.0018, 0.9973666666666666, 0.9938166666666667, 0.9916166666666667, 0.9902500000000001, 0.9890250000000002, 0.9927750000000001, 0.9986, 1.0057333333333334, 1.0038833333333335, 1.0024, 0.9902083333333334, 0.9898833333333333, 0.9856416666666666, 0.9791833333333334, 0.9718083333333335, 0.9809916666666667, 0.9834416666666667, 0.9913166666666667, 0.9962166666666666, 1.0104916666666666, 1.0062666666666666, 1.000925, 1.00455, 1.0043916666666668, 0.9934416666666666, 0.9968749999999998, 1.000275, 0.9995166666666667, 0.9926250000000002, 0.9992916666666667, 1.0030583333333334, 0.9943, 0.9898083333333333, 0.9884249999999999, 0.98545, 0.980975, 0.9890666666666665, 1.001275, 1.00965, 1.0077666666666667, 1.0172166666666669, 1.0222416666666667, 1.015225, 1.0000916666666668, 0.9910083333333334, 0.97015, 0.96125, 0.9573916666666666, 0.965875, 0.97365, 0.9864749999999999, 0.9938250000000001, 1.0003, 1.0029666666666666, 1.0033, 0.9984999999999999, 1.0018333333333331, 1.009575, 1.0136333333333332, 1.020475, 1.02135, 1.018275, 1.0061916666666668, 0.9904583333333333, 0.9831, 0.9735083333333333, 0.9684583333333334, 0.9704583333333333, 0.9809666666666667, 0.9914250000000001, 1.0032583333333334, 1.0096833333333335, 1.015625, 1.0114666666666667, 1.0080916666666666, 1.0013750000000001, 0.9938583333333334, 0.9914583333333334, 0.9925666666666668, 0.9889083333333334, 0.9901000000000001, 0.9882333333333332, 0.9858750000000001, 0.9777166666666668, 0.9693999999999999, 0.973075, 0.9687666666666668, 0.9713833333333334, 0.9763083333333332, 0.9861666666666667, 0.9976583333333333, 1.0038583333333333, 1.0130166666666667, 1.020925, 1.0197500000000002, 1.0177916666666669, 1.0053916666666667, 0.9996916666666666, 0.9887416666666665, 0.9738333333333333, 0.9745500000000001, 0.9819916666666667, 0.9834666666666667, 0.993375, 0.9969416666666667, 1.0026000000000002, 0.9945916666666667, 0.9849500000000001, 0.9864666666666667, 0.9812166666666666, 0.9716, 0.9830166666666665, 0.9862500000000001, 0.9984000000000001, 1.0007833333333334, 1.0110916666666667, 1.0184333333333333, 1.01145, 1.0048083333333333, 1.00105, 0.9922416666666666, 0.9847250000000001, 0.983375, 0.9898416666666666, 0.9968416666666666, 0.995, 1.006541666666667, 1.0046083333333333, 0.9961916666666667, 0.9897916666666666, 0.9851000000000001, 0.9746166666666666, 0.9760249999999999, 0.9852500000000001, 0.9955666666666666, 0.998325, 1.00915, 1.009083333333333, 1.0018, 0.9983749999999999, 0.9950666666666667, 0.9913, 0.9843666666666667, 0.9900083333333333, 1.0001, 0.998525, 0.9956666666666667, 1.0031833333333333, 0.9972416666666667, 0.9841500000000001, 0.9826583333333335], 'hw': [0.6425, 0.8566666666666666, 0.8033333333333332, 0.9558333333333332, 1.1216666666666668, 0.9041666666666665, 1.0033333333333332, 1.1041666666666667, 0.815, 0.5791666666666666, 0.5966666666666667, 0.7599999999999999, 0.5966666666666666, 0.8358333333333333, 1.3283333333333334, 1.2674999999999998, 1.2025, 1.1758333333333333, 1.3083333333333333, 0.8874999999999998, 0.5608333333333332, 0.40166666666666667, 0.2041666666666667, -0.06749999999999996, -0.22166666666666676, -0.2991666666666667, -0.30250000000000005, -0.4741666666666666, -0.5633333333333334, -0.3041666666666667, -0.10166666666666661, -0.1416666666666668, -0.11750000000000009, 0.2850000000000001, 0.18916666666666668, 0.1466666666666665, 0.08916666666666666, 0.37416666666666676, 0.11249999999999993, 0.35749999999999993, 0.73, 0.9158333333333334, 0.7233333333333332, 1.1008333333333333, 1.075, 0.9333333333333331, 0.5008333333333334, 0.4166666666666667, 0.2516666666666667, -0.08333333333333344, -0.25500000000000006, -0.2258333333333333, -0.4825000000000001, -0.6683333333333333, -0.9491666666666667, -0.9316666666666666, -1.015, -0.975, -0.7399999999999999, -0.17166666666666663, 0.01916666666666661, 0.20583333333333323, 0.1516666666666666, 0.27333333333333326, -0.027500000000000007, -0.18083333333333337, -0.39499999999999996, -0.15499999999999994, -0.15999999999999998, 0.3349999999999999, 0.525, 0.6024999999999999, 0.3724999999999999, 0.031666666666666586, -0.16333333333333333, -0.36166666666666664, -0.7475, -0.7741666666666666, -0.7658333333333335, -0.6025, -0.3075, -0.16749999999999998, 0.043333333333333314, -0.09000000000000004, -0.14500000000000002, -0.15916666666666668, -0.35500000000000004, -0.26083333333333325, -0.28166666666666657, -0.31666666666666676, -0.3283333333333334, -0.2874999999999999, -0.4333333333333334, -0.525, -0.8016666666666666, -0.8249999999999998, -1.1508333333333334, -1.1616666666666668, -1.1241666666666665, -0.855, -0.7374999999999999, -0.5375, -0.5441666666666666, -0.54, -0.5674999999999999, -0.5783333333333333, -0.69, -0.7200000000000001, -0.8075, -0.5749999999999998, -0.4124999999999999, -0.2691666666666668, -0.20833333333333337, -0.07500000000000002, -0.2158333333333334, -0.5116666666666668, -0.6083333333333333, -0.7083333333333334, -0.7933333333333333, -0.7483333333333334, -0.2833333333333334, 0.16833333333333342, 0.5058333333333332, 0.8016666666666664, 0.9849999999999999, 1.1191666666666666, 0.9666666666666667, 0.6516666666666666, 0.7308333333333334, 0.8083333333333335, 0.5658333333333333, 0.6399999999999998, 0.7116666666666666, 0.6758333333333333, 0.2591666666666666, 0.21166666666666664, -0.0766666666666666, -0.49416666666666664, -0.7166666666666667, -0.7233333333333333, -0.8541666666666666, -0.6616666666666666, -0.6499999999999999, -0.6858333333333334, -0.4233333333333334, 0.06000000000000005, 0.20333333333333328, 0.34416666666666657, 0.5083333333333333, 0.4724999999999999, 0.13083333333333316, 0.11999999999999995, 0.015000000000000088, -0.2575, -0.17999999999999994, -0.042499999999999954, 0.07499999999999989, 0.2158333333333333, 0.3075, 0.06666666666666664, -0.34500000000000014, -0.38749999999999996, -0.6049999999999999, -0.6475, -0.535, -0.24583333333333343, -0.07833333333333332, 0.1383333333333332, 0.21166666666666667, 0.3166666666666666, 0.06000000000000002, -0.08166666666666664, -0.4008333333333333, -0.46666666666666673, -0.5724999999999999, -0.6725, -0.6258333333333334, -0.3291666666666666, -0.2675, -0.21500000000000016, 0.00916666666666662, -0.04916666666666666, -0.18916666666666673, -0.3658333333333334, -0.45249999999999996, -0.5599999999999999, -0.5025000000000001, -0.3491666666666666, 0.06499999999999999, 0.16, 0.2583333333333332, 0.0783333333333332, 0.09583333333333321, -0.14833333333333332, -0.36916666666666664, -0.6358333333333334, -0.36916666666666664, -0.42749999999999994, -0.17000000000000007, 0.05083333333333332, 0.4383333333333334, 0.3183333333333333, 0.09166666666666652, 0.2125, 0.1416666666666667, -0.00833333333333334, 0.22749999999999995, 0.4625000000000001, 0.5016666666666666, 0.4449999999999999, 0.755, 0.8249999999999998, 0.5083333333333333, 0.36916666666666664, 0.13249999999999998, -0.16250000000000006, -0.30083333333333345, -0.14416666666666664, -0.0124999999999999, 0.13499999999999998, 0.22916666666666666, 0.4666666666666666, 0.59, 0.44916666666666666, 0.13999999999999993, -0.18333333333333338, -0.7374999999999999, -0.9750000000000001, -1.2141666666666666, -1.1366666666666667, -1.0491666666666666, -0.8441666666666667, -0.7433333333333333, -0.66, -0.5449999999999999, -0.48583333333333334, -0.5483333333333333, -0.30666666666666664, 0.04833333333333336, 0.2566666666666666, 0.4933333333333332, 0.7358333333333332, 0.9241666666666667, 0.7008333333333333, 0.3841666666666666, 0.3791666666666668, 0.07416666666666671, -0.030833333333333417, 0.016666666666666607, 0.39333333333333326, 0.5233333333333333, 0.7433333333333332, 0.8433333333333333, 0.9408333333333333, 0.6241666666666666, 0.4358333333333333, -0.031666666666666655, -0.23416666666666666, -0.3091666666666666, -0.2841666666666665, -0.38416666666666677, -0.2691666666666667, -0.18666666666666668, -0.28250000000000003, -0.43916666666666676, -0.6233333333333334, -0.64, -0.8775000000000001, -0.8408333333333333, -0.805, -0.67, -0.44083333333333324, -0.3083333333333333, -0.12833333333333338, 0.2733333333333334, 0.3758333333333333, 0.3658333333333332, 0.18333333333333315, 0.16666666666666666, -0.15833333333333335, -0.6516666666666667, -0.5716666666666667, -0.3066666666666666, -0.4333333333333334, -0.16750000000000012, 0.09333333333333327, 0.21416666666666662, -0.022500000000000114, -0.3316666666666667, -0.21333333333333318, -0.4816666666666665, -0.7841666666666667, -0.43249999999999994, -0.2441666666666665, 0.06999999999999995, 0.4091666666666667, 0.7558333333333334, 1.015, 0.8474999999999998, 0.6933333333333334, 0.4725, 0.052499999999999956, -0.17999999999999994, -0.27499999999999997, -0.3166666666666666, 0.07916666666666662, 0.21666666666666665, 0.5241666666666666, 0.5775, 0.4433333333333332, 0.20083333333333328, -0.045833333333333365, -0.42999999999999994, -0.3558333333333333, -0.22833333333333328, 0.03666666666666659, 0.1774999999999999, 0.42083333333333334, 0.4433333333333333, 0.22499999999999987, 0.06333333333333324, -0.09999999999999994, -0.36999999999999994, -0.7291666666666666, -0.5216666666666666, -0.345, -0.4133333333333334, -0.4833333333333334, -0.1875, -0.3033333333333333, -0.6125000000000002, -0.6541666666666667], 'em': [1.1040416666666666, 1.160975, 1.1457750000000002, 1.176925, 1.2351916666666667, 1.1968333333333334, 1.1934666666666667, 1.2361166666666668, 1.221125, 1.1649333333333332, 1.1814583333333333, 1.2071833333333335, 1.1631083333333332, 1.156175, 1.2253749999999999, 1.1866083333333333, 1.125, 1.0907916666666668, 1.09915, 1.0098749999999999, 0.9696999999999999, 0.9522666666666666, 0.9361666666666665, 0.9332833333333331, 0.9427250000000001, 0.9846916666666665, 1.0258833333333335, 1.0446, 1.0336916666666667, 1.0580416666666668, 1.1059416666666666, 1.0716833333333333, 1.0131416666666666, 1.0577750000000001, 1.0078666666666667, 0.9858333333333333, 0.9573333333333333, 1.0229916666666667, 1.019116666666667, 1.068125, 1.1437583333333334, 1.2118166666666665, 1.1831333333333334, 1.241666666666667, 1.196625, 1.1868666666666667, 1.0950833333333334, 1.0513583333333332, 1.0204000000000002, 0.9664416666666665, 0.9499583333333333, 0.9660916666666667, 0.9338583333333332, 0.9418749999999999, 0.922875, 0.9169, 0.9256000000000002, 0.9247583333333332, 0.9636666666666666, 1.008875, 1.0357666666666667, 1.0431166666666665, 1.000025, 0.9952666666666667, 0.94875, 0.9107249999999999, 0.9114333333333332, 0.9502833333333335, 0.9404, 1.036225, 1.0672, 1.0608000000000002, 0.9992666666666666, 0.9711500000000001, 0.9255249999999998, 0.8887916666666666, 0.8844583333333332, 0.9393166666666666, 0.9666, 1.044675, 1.131075, 1.154125, 1.1779249999999999, 1.1481, 1.0987916666666666, 1.0584666666666667, 0.9998083333333332, 0.9849583333333332, 0.9671666666666666, 0.9691249999999999, 0.9944416666666666, 0.987575, 0.9740166666666669, 0.9831, 0.9020666666666667, 0.875825, 0.8366666666666666, 0.8226416666666667, 0.8257333333333333, 0.8726249999999999, 0.908525, 0.9572749999999998, 0.9821416666666667, 1.00275, 0.9890000000000002, 0.9408166666666667, 0.8976583333333332, 0.8207333333333334, 0.7599, 0.7633083333333334, 0.7735583333333332, 0.7753333333333333, 0.7898999999999999, 0.838675, 0.8549583333333333, 0.8504333333333333, 0.8746333333333333, 0.8878083333333334, 0.90985, 0.9478333333333332, 1.0416416666666668, 1.1293083333333331, 1.1540249999999999, 1.2073500000000001, 1.1826999999999999, 1.1759833333333334, 1.1372333333333333, 1.0876916666666667, 1.0788916666666666, 1.0921416666666668, 1.0497583333333333, 1.0728666666666669, 1.0474666666666668, 1.0583833333333335, 1.0057583333333333, 0.9943333333333332, 0.9789499999999999, 0.9355666666666665, 0.9205166666666668, 0.8972833333333335, 0.8544416666666667, 0.8636499999999999, 0.8260166666666667, 0.7732083333333334, 0.8102916666666666, 0.8619750000000002, 0.9203250000000001, 0.9735333333333331, 1.0385416666666665, 1.064525, 1.04485, 1.05475, 1.0463333333333333, 0.99565, 1.002525, 1.0072083333333335, 1.015975, 1.0106666666666666, 1.022525, 0.9675416666666666, 0.914575, 0.8821499999999999, 0.8613500000000002, 0.86515, 0.8937249999999999, 0.9097666666666667, 0.9842916666666666, 1.054675, 1.1047833333333335, 1.131825, 1.1531749999999998, 1.1645333333333334, 1.1384583333333333, 1.09845, 1.0973416666666667, 1.0582333333333331, 1.0163416666666667, 1.0115666666666667, 0.9876333333333331, 0.9566499999999999, 0.9610249999999999, 0.9204416666666665, 0.9140999999999999, 0.8696333333333334, 0.8480083333333334, 0.8682583333333334, 0.8865333333333334, 0.9348166666666667, 1.0126, 1.0295166666666666, 1.0550499999999998, 1.0216166666666668, 1.0272749999999997, 0.9665666666666667, 0.9031250000000001, 0.8261833333333333, 0.8242583333333333, 0.7849333333333334, 0.8234916666666665, 0.8404166666666666, 0.9277666666666665, 0.9207333333333333, 0.946275, 0.981575, 1.0009833333333333, 0.9890333333333333, 1.0333833333333333, 1.0326000000000002, 1.0258916666666666, 0.9753583333333333, 1.0108666666666668, 1.0090666666666668, 0.9947416666666665, 1.002, 1.0184749999999998, 0.9975916666666667, 1.0248083333333333, 1.0578416666666668, 1.1014333333333333, 1.0897083333333333, 1.0711916666666665, 1.0954833333333334, 1.0978333333333332, 1.047675, 0.9836916666666666, 0.9179083333333332, 0.8231583333333333, 0.7909749999999999, 0.7844166666666667, 0.8121666666666667, 0.8480166666666668, 0.9018916666666666, 0.93555, 0.9535666666666666, 0.9781666666666666, 0.9536083333333331, 0.9277166666666666, 0.9345500000000001, 0.9893583333333335, 0.9794416666666667, 1.0312416666666666, 1.0296, 1.0580999999999998, 1.0061916666666668, 0.983925, 0.9601750000000001, 0.9243416666666665, 0.9200416666666666, 0.9300416666666668, 0.9618666666666668, 1.0284666666666666, 1.0698916666666667, 1.0907666666666664, 1.1046083333333332, 1.0963583333333333, 1.1043833333333335, 1.0478583333333333, 1.0577666666666667, 1.0692166666666667, 1.0837750000000002, 1.0597416666666666, 1.0662416666666665, 1.0253333333333332, 0.949725, 0.8533249999999999, 0.806725, 0.7801416666666666, 0.7530083333333334, 0.7707916666666667, 0.819575, 0.8696083333333334, 0.9442166666666666, 0.9950000000000001, 1.01365, 1.0570000000000002, 1.0558166666666666, 1.0415416666666666, 0.9805333333333334, 0.9584083333333332, 0.8760499999999999, 0.8163499999999999, 0.8231999999999999, 0.8765583333333332, 0.8681833333333332, 0.9547583333333334, 0.974225, 1.0146, 0.9834666666666667, 0.9707833333333334, 0.9473916666666667, 0.9243166666666668, 0.8747583333333333, 0.92935, 0.9258333333333333, 0.9797249999999998, 1.0100083333333332, 1.0641666666666667, 1.0614499999999998, 1.0290416666666669, 0.9990083333333333, 0.9536416666666666, 0.8569916666666666, 0.8426, 0.83515, 0.8545666666666666, 0.9419416666666667, 0.9971416666666665, 1.064775, 1.0633416666666666, 1.0271333333333332, 0.9642333333333334, 0.8801166666666668, 0.8191416666666668, 0.8062, 0.820125, 0.8655749999999999, 0.8925166666666667, 0.9427583333333333, 0.9364333333333331, 0.9054916666666667, 0.9061749999999998, 0.8604916666666668, 0.8441166666666667, 0.8369249999999999, 0.8966333333333334, 0.9358666666666666, 0.9529166666666667, 0.9545416666666666, 1.0000833333333334, 0.9622999999999999, 0.9466916666666667, 0.9502083333333333], 'ja': [-0.2865, -0.4025, -0.38666666666666666, -0.45041666666666663, -0.5534166666666666, -0.4915, -0.5179999999999999, -0.5986666666666668, -0.5644166666666667, -0.4741666666666666, -0.5188333333333334, -0.5486666666666666, -0.4499166666666667, -0.4429166666666666, -0.5974166666666667, -0.50125, -0.4111666666666666, -0.36474999999999996, -0.37866666666666665, -0.18374999999999997, -0.09016666666666662, -0.03808333333333336, 0.02849999999999997, 0.07450000000000001, 0.055583333333333325, -0.015416666666666653, -0.08675, -0.122, -0.10616666666666669, -0.148, -0.2265, -0.17208333333333334, -0.07041666666666667, -0.16366666666666665, -0.09008333333333333, -0.06491666666666664, -0.026083333333333302, -0.1643333333333333, -0.15775, -0.26099999999999995, -0.41124999999999995, -0.53475, -0.4717499999999999, -0.5947499999999999, -0.5158333333333333, -0.48241666666666655, -0.3039166666666666, -0.22441666666666663, -0.18141666666666667, -0.0809166666666667, -0.06258333333333332, -0.09566666666666666, -0.038250000000000006, -0.032333333333333325, 0.017416666666666678, 0.04558333333333331, 0.04883333333333331, 0.09541666666666666, 0.033499999999999995, -0.06475, -0.11866666666666666, -0.14083333333333334, -0.08041666666666662, -0.08091666666666664, 0.003583333333333346, 0.07575, 0.08016666666666668, 0.021916666666666685, 0.05833333333333335, -0.14616666666666667, -0.2186666666666667, -0.22666666666666666, -0.14575000000000002, -0.11633333333333336, -0.026083333333333347, 0.059250000000000004, 0.08816666666666663, 0.019416666666666638, 0.024333333333333318, -0.10108333333333334, -0.28708333333333336, -0.34308333333333335, -0.4008333333333333, -0.36999999999999994, -0.3100833333333333, -0.22858333333333336, -0.11625, -0.0974166666666667, -0.0561666666666667, -0.018833333333333303, -0.06624999999999998, -0.0628333333333333, -0.031083333333333324, -0.03541666666666667, 0.10525, 0.15666666666666665, 0.24391666666666664, 0.2665, 0.26066666666666666, 0.18641666666666667, 0.12908333333333333, 0.04675000000000001, 0.005333333333333338, -0.030583333333333337, -0.010333333333333325, 0.06633333333333334, 0.1370833333333333, 0.26225000000000004, 0.3594166666666667, 0.35233333333333333, 0.3333333333333333, 0.3253333333333333, 0.3005, 0.22091666666666665, 0.1935, 0.20533333333333334, 0.16749999999999998, 0.14774999999999996, 0.06616666666666664, -0.011000000000000024, -0.205, -0.39175, -0.4651666666666667, -0.5498333333333333, -0.5093333333333333, -0.49991666666666673, -0.4141666666666666, -0.2800833333333333, -0.28125, -0.3060833333333333, -0.22574999999999998, -0.26699999999999996, -0.23016666666666663, -0.23658333333333328, -0.11341666666666667, -0.07925, -0.03908333333333333, 0.060666666666666653, 0.10433333333333335, 0.14425, 0.21525000000000002, 0.1780833333333333, 0.2306666666666667, 0.30625, 0.23441666666666672, 0.136, 0.044583333333333336, -0.044999999999999984, -0.14891666666666667, -0.1875833333333333, -0.13441666666666666, -0.14825000000000002, -0.12666666666666665, -0.03683333333333333, -0.042583333333333334, -0.043750000000000004, -0.05133333333333332, -0.042416666666666686, -0.06175000000000002, 0.026583333333333337, 0.11183333333333334, 0.16158333333333333, 0.19391666666666665, 0.17024999999999998, 0.11599999999999998, 0.08125, -0.05316666666666666, -0.18183333333333332, -0.25891666666666663, -0.3000833333333333, -0.32975, -0.3405833333333333, -0.2753333333333333, -0.2060833333333333, -0.19866666666666666, -0.12625, -0.05208333333333334, -0.04716666666666668, -0.010083333333333333, 0.038166666666666654, 0.03133333333333333, 0.09491666666666665, 0.10758333333333332, 0.17975, 0.21675, 0.18433333333333332, 0.15508333333333335, 0.076, -0.07366666666666666, -0.11325, -0.1638333333333333, -0.11966666666666666, -0.13483333333333333, -0.02808333333333333, 0.08558333333333334, 0.22291666666666665, 0.23383333333333334, 0.32075, 0.26083333333333336, 0.2343333333333333, 0.045333333333333316, 0.037749999999999985, -0.020083333333333304, -0.09816666666666667, -0.15033333333333335, -0.13616666666666669, -0.21816666666666665, -0.21733333333333338, -0.2040833333333333, -0.08791666666666663, -0.16416666666666666, -0.16049999999999998, -0.12991666666666665, -0.13649999999999998, -0.14458333333333329, -0.0875833333333333, -0.1199166666666666, -0.17991666666666664, -0.24525, -0.2091666666666667, -0.18016666666666667, -0.23, -0.22824999999999998, -0.14466666666666667, -0.03466666666666668, 0.07716666666666665, 0.24941666666666665, 0.30108333333333337, 0.3125833333333333, 0.2675, 0.20775, 0.11825000000000001, 0.062416666666666676, 0.033166666666666685, -0.005083333333333329, 0.03766666666666666, 0.084, 0.0755, -0.014583333333333328, 0.003250000000000012, -0.08174999999999998, -0.07975, -0.14916666666666664, -0.07308333333333333, -0.044583333333333336, -0.01733333333333331, 0.03208333333333335, 0.024500000000000022, 0.0067500000000000155, -0.04833333333333332, -0.16349999999999995, -0.2350833333333333, -0.2685833333333333, -0.2915833333333333, -0.2755, -0.28125, -0.15541666666666662, -0.16000000000000003, -0.16749999999999998, -0.1799166666666667, -0.12583333333333335, -0.1395833333333333, -0.07608333333333334, 0.04366666666666663, 0.19791666666666666, 0.27375, 0.32049999999999995, 0.36766666666666664, 0.34391666666666665, 0.2670833333333333, 0.1894166666666667, 0.02074999999999999, -0.07633333333333338, -0.12366666666666666, -0.23458333333333334, -0.2619166666666667, -0.23091666666666666, -0.12749999999999997, -0.08375, 0.07258333333333333, 0.22583333333333333, 0.22058333333333333, 0.14175000000000001, 0.17741666666666667, 0.034749999999999996, 0.0021666666666666687, -0.06649999999999999, -0.01791666666666668, 0.0027499999999999933, 0.0390833333333333, 0.07891666666666668, 0.16158333333333333, 0.027416666666666634, 0.01849999999999996, -0.08783333333333332, -0.1749166666666667, -0.28750000000000003, -0.2741666666666666, -0.21174999999999997, -0.15466666666666667, -0.05325, 0.15866666666666665, 0.19083333333333333, 0.21158333333333335, 0.2001666666666667, 0.03400000000000001, -0.06541666666666666, -0.18333333333333335, -0.18766666666666665, -0.1365833333333333, -0.024083333333333328, 0.12208333333333332, 0.23241666666666663, 0.2611666666666667, 0.25775, 0.18216666666666667, 0.13525, 0.05199999999999997, 0.06275000000000001, 0.11525, 0.11691666666666667, 0.19241666666666665, 0.22366666666666668, 0.23916666666666667, 0.14133333333333334, 0.07925, 0.05125000000000001, 0.04725000000000001, -0.07591666666666669, -0.033583333333333354, -0.026666666666666606, -0.05491666666666667]}
    recommended_name
    sequence MSELDQLRQEAEQLKNQIRDARKACADATLSQITNNIDPVGRIQMRTRRTLRGHLAKIYAMHWGTDSRLLVSASQDGKLIIWDSYTTNKVHAIPLRSSWVMTCAYAPSGNYVACGGLDNICSIYNLKTREGNVRVSRELAGHTGYLSCCRFLDDNQIVTSSGDTTCALWDIETGQQTTTFTGHTGDVMSLSLAPDTRLFVSGACDASAKLWDVREGMCRQTFTGHESDINAICFFPNGNAFATGSDDATCRLFDLRADQELMTYSHDNIICGITSVSFSKSGRLLLAGYDDFNCNVWDALKADRAGVLAGHDNRVSCLGVTDDGMAVATGSWDSFLKIWN
    settings <michelanglo_protein.settings_handler.GlobalSettings object at 0x7f65547474e0>
    swissmodel [<michelanglo_protein.core.Structure object at 0x7f654b69f438>]
    timestamp 2020-01-02 16:07:50.872591
    uniprot P62873
    uniprot_dataset Swiss-Prot
    uniprot_name GBB1_HUMAN
    version 1.0
    xml <NewElement '{http://uniprot.org/uniprot}entry' at 0x7f654b69f4e0>
    """
    settings = global_settings
    version = 1.0 #this is for pickled file migration/maintenance.

    def __init__(self, gene_name='', uniprot = '', uniprot_name = '', sequence='', organism = None, taxid=None, **other):
        ### predeclaration (and cheatsheet)
        if organism: # dictionary with keys common scientific and NCBI Taxonomy
            self.organism = organism
        else:
            self.organism = {'common': 'NA', 'scientific': 'NA', 'NCBI Taxonomy': 'NA', 'other': 'NA'} ##obs? ignore for human purposes.
        if taxid:
            self.organism['NCBI Taxonomy'] = taxid
        self.gene_name = gene_name
        self.uniprot_name = uniprot_name.strip() ## S39AD_HUMAN
        #### uniprot derivved
        self.uniprot = uniprot.strip() ## uniprot accession
        self.uniprot_dataset = '' ## Swiss-Prot good, TrEMBL bad.
        self.alt_gene_name_list = []
        self.accession_list = [] ## Q96H72 etc.
        self.sequence = sequence  ###called seq in early version causing eror.rs
        self.recommended_name = '' #Zinc transporter ZIP13
        self.alternative_fullname_list = []
        self.alternative_shortname_list = []
        self.properties={}
        self.features={}  #see _parse_protein_feature. Dictionary of key: type of feature, value = list of dict with the FeatureViewer format (x,y, id, description)
        self.partners ={'interactant': [],  #from uniprot
                        'BioGRID': [],  #from biogrid downlaoad
                        'SSL': [],  #Slorth data
                        'HuRI': [],
                        'stringDB highest': [],  # score >900
                        'stringDB high': [],  #900 > score > 700
                        'stringDB medium': [], #400 > score > 400
                        'stringDB low': [] #score < 400
                        } # lists not sets as it gave a pickle issue.
        self.diseases=[] # 'description', 'name', 'id', 'MIM'
        self.pdbs = []  # {'description': elem.attrib['id'], 'id': elem.attrib['id'], 'x': loca[0], 'y': loca[1]}
        self.ENSP = ''
        self.ENST = ''
        self.ENSG = ''
        ### ExAC
        self.gnomAD = [] #formerlly alleles
        #self.ExAC_type (property= 'Unparsed' # Dominant | Recessive | None | Unknown (=???)
        self.pLI = -1
        self.pRec = -1
        self.pNull = -1
        ### pdb
        self.pdb_matches =[] #{'match': align.title[0:50], 'match_score': hsp.score, 'match_start': hsp.query_start, 'match_length': hsp.align_length, 'match_identity': hsp.identities / hsp.align_length}
        self.swissmodel = []
        self.percent_modelled = -1
        ### junk
        self.other = other ### this is a garbage bin. But a handy one.
        self.logbook = [] # debug purposes only. See self.log()
        self._threads = {}
        self.timestamp = datetime.now()
        #not needed for ProteinLite
        self.xml = None

    ############################## property objects

    @property
    def ExAC_type(self):
        if self.pLI < 0:  # error.
            return 'Unknown'
        elif self.pLI > max(self.pRec, self.pNull):
            return 'Dominant'
        elif self.pRec > max(self.pLI, self.pNull):
            return 'Recessive'
        elif self.pNull > max(self.pLI, self.pRec):
            return 'None'
        else:
            return 'Unknown'

    ############################# IO #############################
    def _get_species_folder(self):
        if self.organism['NCBI Taxonomy'] == 'NA':
            self.log(f'NA Species??! {self.organism} for {self.uniprot_name}')
            species = f'taxid{self.get_species_for_uniprot()}'
        else:
            species = f'taxid{self.organism["NCBI Taxonomy"]}'
        path = os.path.join(self.settings.pickle_folder, species)
        if not os.path.exists(path):
            os.mkdir(path)
        return path


    def exists(self, file=None):
        """
        Method to check if file exists already.
        Actually loads it sneakily!
        :return:
        """
        if file is not None:
            return os.path.exists(file)
        try:
            path = self._get_species_folder()
        except ValueError:
            return False
        for extension, loader in (('.p', self.load), ('.pgz', self.gload)):
            file = os.path.join(path, self.uniprot + extension)
            if os.path.exists(file):
                loader()
                return True
        return False

    def dump(self, file=None):
        if not file:
            path = self._get_species_folder()
            file = os.path.join(path, '{0}.p'.format(self.uniprot))
        self.complete()  # wait complete.
        pickle.dump(self.__dict__, open(file, 'wb'))
        self.log('Data saved to {} as pickled dictionary'.format(file))

    def gdump(self, file=None):
        if not file:
            path = self._get_species_folder()
            file = os.path.join(path,  f'{self.uniprot}.pgz')
        self.complete()  # wait complete.
        if not os.path.exists(path):
            os.mkdir(path)
        with gzip.GzipFile(file, 'w') as f:
            pickle.dump(self.__dict__, f)
        self.log('Data saved to {} as gzipped pickled dictionary'.format(file))

    def get_species_for_uniprot(self):
        warn('You have triggered a fallback. If you know your filepath to load use it.')
        uniprot2species = json.load(open(os.path.join(self.settings.dictionary_folder, 'uniprot2species.json')))
        if self.uniprot in uniprot2species.keys():
            return uniprot2species[self.uniprot]
        else:
            raise ValueError('Cannot figure out species of uniprot to load it. Best bet is to fetch it.')

    #decorator /fake @classmethod
    def _ready_load(fun):
        """
        Prepare loading for both load and gload.
        Formerly allowed it to run as a class method, code not fixed.
        :return:
        """
        def loader(self, file=None):
            if not file:
                path = self._get_species_folder()
                if fun.__name__ == 'load':
                    extension = '.p'
                else:
                    extension = '.pgz'
                file = os.path.join(path, self.uniprot+extension)
            fun(self, file)
            return self
        return loader

    @_ready_load
    def load(self, file):
        self.__dict__ = pickle.load(open(file, 'rb'))
        self.log('Data from the pickled dictionary {}'.format(file))
        return self

    @_ready_load
    def gload(self, file):
        with gzip.GzipFile(file, 'r') as f:
            self.__dict__ = pickle.load(f)
        self.log('Data from the gzipped pickled dictionary {}'.format(file))
        return self

    ####################### Misc Magic methods ##################
    def __len__(self):  ## sequence lenght
        return len(self.sequence)

    def log(self, text):
        """
        Logging is primarily for protein_full
        :param text:
        :return:
        """
        msg = '[{}]\t'.format(str(datetime.now())) + text
        self.logbook.append(msg)
        if self.settings.verbose:
            print(msg)
        return self

    def __str__(self):
        if len(self.gene_name):
            return self.gene_name
        else:
            return self.uniprot

    def complete(self):
        """
        Make sure that all subthreads are complete. Not used for Core!
        """
        for k in self._threads:
            if self._threads[k] and self._threads[k].is_alive():
                self._threads[k].join()
        self._threads = {}
        return self

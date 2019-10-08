__description__ = """
This is the handler for the settings to control where to save stuff, etc.
It allows customisation of output if the script is not running on a server.
The key parts are:




Note that the folder pages (.pages_folder) was for when it was not for a server. .wipe_html() clears them.
"""
################## Environment ###########################

import os, json
from pprint import PrettyPrinter

#these are needed for reference file retrieval
import urllib, gzip, shutil, tarfile

pprint = PrettyPrinter().pprint
from warnings import warn

class Singleton(type): #https://stackoverflow.com/questions/6760685/creating-a-singleton-in-python
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        else:
            warn('Attempt to initialise another instance of a singleton. Returning original.')
        return cls._instances[cls]

class GlobalSettings(metaclass=Singleton):
    """
    This class is container for the paths, which are used by both Variant and Tracker classes.
    Hence why in these two is the attribute .settings
    """
    verbose = False
    subdirectory_names = ('reference', 'temp', 'uniprot','pdbblast', 'pickle', 'binders', 'dictionary')

                          #'manual', 'transcript', 'protein', 'uniprot', 'pfam', 'pdb', 'ELM', 'ELM_variant', 'pdb_pre_allele', 'pdb_post_allele', 'ExAC', 'pdb_blast', 'pickle', 'references', 'go',
                          #'binders')
    fetch = True
    missing_attribute_tolerant = True
    error_tolerant = False
    addresses = ('ftp://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.xml.gz',
                 'ftp://ftp.ncbi.nlm.nih.gov/blast/db/pdbaa.tar.gz',
                 'ftp://ftp.broadinstitute.org/pub/ExAC_release/release1/functional_gene_constraint/fordist_cleaned_exac_r03_march16_z_pli_rec_null_data.txt',
                 'ftp://ftp.ebi.ac.uk/pub/databases/msd/sifts/flatfiles/tsv/pdb_chain_uniprot.tsv.gz',
                 'ftp://ftp.wwpdb.org/pub/pdb/derived_data/index/resolu.idx',
                 'https://swissmodel.expasy.org/repository/download/core_species/9606_meta.tar.gz',
                 'https://storage.googleapis.com/gnomad-public/release/2.1.1/vcf/genomes/gnomad.genomes.r2.1.1.exome_calling_intervals.sites.vcf.bgz',
                 'https://storage.googleapis.com/gnomad-public/release/2.1.1/vcf/exomes/gnomad.exomes.r2.1.1.sites.vcf.bgz')

    # getter of data_folder
    def _get_datafolder(self):
        if not self._initialised:
            self.init()
        return self._datafolder

    # setter of data_folder
    def _set_datafolder(self, new_folder):
        self.data_subdirectories = []
        self._datafolder = new_folder
        if not os.path.isdir(new_folder):
            os.mkdir(new_folder)
        for directory in self.subdirectory_names:
            if new_folder:
                path = os.path.join(new_folder, directory)
            else:
                path = directory
                warn('Setting the data directory to the base directory is a stupid idea.')
            self.data_subdirectories.append(path)
            setattr(self, directory + '_folder', path)
            if not os.path.isdir(path):
                os.mkdir(path)

    data_folder = property(_get_datafolder, _set_datafolder)

    def __init__(self, home_url='/'):
        self._obodict={}
        self.home_url = home_url
        self._initialised = False

    def init(self, data_folder='data'):
        if self._initialised:
            raise Exception('The module is already initialised.')
        self._initialised = True
        self.data_folder = data_folder
        #self.page_folder = page_folder  # does nothing.
        print(f'Folder path set to {self.data_folder}')
        return self


    def get_folder_of(self, name):
        if not self._initialised:
            self.init()
        return getattr(self, name + '_folder')

    def degunk(self):
        """
        Removes the zero sized files that may ahve arised from error or keyboard interuption.
        :return:
        """
        for dir in self.data_subdirectories:
            for file in os.listdir(dir):
                if os.stat(os.path.join(dir, file)).st_size < 100 and not os.path.isdir(
                        os.path.join(dir, file)):
                    if self.verbose: print('Removing file {}'.format(file))
                    os.remove(os.path.join(dir, file))
        if self.verbose: print('clean-up complete')

    def wipe_html(self):
        """
        No longer needed.
        :return:
        """
        for file in os.listdir(self.page_folder):
            if '.htm' in file or '.pdb' in file:
                os.remove(os.path.join(self.page_folder, file))

    def retrieve_references(self, ask = True, refresh=False, issue = ''):
        if not self._initialised:
            raise ValueError('You have not initialised the settings (set the folder) >>> run xxx.settings.init()')
        if ask:
            print('*' * 20)
            print('CORE reference DATA IS MISSING --trigger by '+issue)
            print('There are two options, you have never ever run this script before or the folder {0} is not corrent'.format(self.reference_folder))
            print('this is super experimental (i.e. I\'ve never bother)')
            i = input('Continue y/[n] _')
            if not i or i in ('N', 'n'):
                print('Exiting...')
                exit()
        for url in self.addresses:
            file = os.path.join(self.reference_folder, os.path.split(url)[1])
            if os.path.isfile(file) and not refresh:
                if self.verbose:
                    print('{0} file is present already'.format(file))
            else:
                if self.verbose:
                    print('{0} file is being downloaded'.format(file))
                self._get_url(url, file)
            self._unzip_file(file)
        ## convert dodgy ones.
        self.create_json_from_idx('resolu.idx', 'resolution.json')

        #implement cat *.psi > cat.psi where psi files are from http://interactome.baderlab.org/data/')

    def _get_url(self, url, file):
        req = urllib.request.Request(url)
        response = urllib.request.urlopen(req)
        data = response.read()
        with open(file, 'wb') as w:
            w.write(data)

    def _unzip_file(self, file):
        unfile = file.replace('.gz', '').replace('.tar', '')
        if '.tar.gz' in file:
            if not os.path.exists(unfile):
                os.mkdir(unfile)
                tar = tarfile.open(file)
                tar.extractall(path=unfile)
                tar.close()
            elif self.verbose: print('{0} file is already decompressed'.format(file))
        elif '.gz' in file:  #ignore the .bgz of gnomad. it is too big.
            if not os.path.isfile(unfile):
                if self.verbose:
                    print('{0} file is being extracted to {1}'.format(file, unfile))
                with open(unfile, 'wb') as f_out:
                    with gzip.open(file, 'rb') as f_in:
                        shutil.copyfileobj(f_in, f_out)
            elif self.verbose: print('{0} file is already decompressed'.format(file))
        else:
            pass #not a compressed file
        return self

    def _open_reference(self, file, mode='r'):
        fullfile = os.path.join(self.reference_folder, file)
        if mode == 'w':
            return open(fullfile, 'w')
        elif not os.path.isfile(fullfile):
            self.retrieve_references(issue = fullfile)
        ## handle compression

        return open(fullfile)

    def open(self, kind):
        kdex = {'ExAC_pLI': 'fordist_cleaned_exac_r03_march16_z_pli_rec_null_data.txt',
                'ExAC_vep': 'ExAC.r1.sites.vep.vcf',
                'ID_mapping': 'HUMAN_9606_idmapping_selected.tab',
                'ssl': 'h.sapiens_ssl_predictions.csv',
                'go': 'go.obo',
                'go_human': 'goa_human.gaf',
                'huri':'cat.psi',
                'biogrid':'BIOGRID-ALL-3.5.166.mitab.txt',
                'string':'9606.protein.links.v10.5.txt',
                'ensembl':'ensemb.txt',
                'nextprot':'nextprot_refseq.txt',
                'swissmodel':'9606_meta/SWISS-MODEL_Repository/INDEX.json',
                'pdb_chain_uniprot': 'pdb_chain_uniprot.tsv',
                'elm':'elm_classes.tsv',
                'resolution': 'resolution.json'}
        assert kind in kdex, 'This is weird. unknown kind, should be: {0}'.format(list(kdex.keys()))
        return self._open_reference(kdex[kind])

    def create_json_from_idx(self, infile, outfile):
        # resolu.idx is in the weirdest format.
        fh = self._open_reference(infile)
        for row in fh:
            if not row.strip():
                break
        header = next(fh).split()
        next(fh) #dashes
        parts = [dict(zip(header, [f.strip() for f in row.split(';')])) for row in fh if row.strip()]
        json.dump(parts, self. _open_reference(outfile, mode='w'))

global_settings = GlobalSettings()
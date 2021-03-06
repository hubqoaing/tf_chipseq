#!/usr/bin/env python
# accessionator 0.0.1
# Generated by dx-app-wizard.
#
# Basic execution pattern: Your app will run on a single machine from
# beginning to end.
#
# See https://wiki.dnanexus.com/Developer-Portal for documentation and
# tutorials on how to modify this file.
#
# DNAnexus Python Bindings (dxpy) documentation:
#   http://autodoc.dnanexus.com/bindings/python/current/

import os, requests, logging, re, urlparse, subprocess, requests, json, shlex, time
import dxpy

try:
    DX_FS_ROOT = os.environ['DX_FS_ROOT']
except:
    DX_FS_ROOT = ""
KEYFILE = DX_FS_ROOT + '/keypairs.json'

GET_MAX_TRIES = 5
GET_TRY_DELAY = 5
DEFAULT_SERVER = 'https://www.encodeproject.org'
logger = logging.getLogger(__name__)

FILE_OBJ_TEMPLATE = {
        'lab': 'j-michael-cherry',
        'award': 'U41HG006992',
        'file_format': 'bam',
        'output_type': 'alignments'
}


def processkey(key):

    if key:
        keysf = open(KEYFILE,'r')
        keys_json_string = keysf.read()
        keysf.close()
        keys = json.loads(keys_json_string)
        logger.debug("Keys: %s" %(keys))
        key_dict = keys[key]
    else:
        key_dict = {}
    AUTHID = key_dict.get('key')
    AUTHPW = key_dict.get('secret')
    if key:
        SERVER = key_dict.get('server')
    else:
        SERVER = 'https://www.encodeproject.org/'

    if not SERVER.endswith("/"):
        SERVER += "/"

    return (AUTHID,AUTHPW,SERVER)

def encoded_get(url, AUTHID=None, AUTHPW=None):
    HEADERS = {'content-type': 'application/json'}
    tries_left = GET_MAX_TRIES
    while tries_left:
        tries_left -= 1
        if AUTHID and AUTHPW:
            try:
                response = requests.get(url, auth=(AUTHID,AUTHPW), headers=HEADERS)
                response.raise_for_status()
                break
            except:
                logger.warning("GET %s failed ... Retry in %d seconds" %(url, GET_TRY_DELAY))
                time.sleep(GET_TRY_DELAY)
                continue    
        else:
            try:
                response = requests.get(url, headers=HEADERS)
                response.raise_for_status()
                break
            except:
                logger.warning("GET %s failed ... Retry in %d seconds" %(url, GET_TRY_DELAY))
                time.sleep(GET_TRY_DELAY)
                continue    
    
    return response

def encoded_post(url, AUTHID, AUTHPW, payload):
    HEADERS = {'content-type': 'application/json'}
    response = requests.post(url, auth=(AUTHID,AUTHPW), headers=HEADERS, data=json.dumps(payload))
    return response

def flagstat_parse(flagstat_file):
    if not flagstat_file:
        return None

    qc_dict = { #values are regular expressions, will be replaced with scores [hiq, lowq]
        'in_total': 'in total',
        'mapped': 'mapped',
        'paired_in_sequencing': 'paired in sequencing',
        'read1': 'read1',
        'read2': 'read2',
        'properly_paired': 'properly paired',
        'with_self_mate_mapped': 'with itself and mate mapped',
        'singletons': 'singletons',
        'mate_mapped_different_chr': 'with mate mapped to a different chr$', #i.e. at the end of the line
        'mate_mapped_different_chr_hiQ': 'with mate mapped to a different chr \(mapQ>=5\)' #RE so must escape
    }
    flagstat_lines = flagstat_file.read().splitlines()
    for (qc_key, qc_pattern) in qc_dict.items():
        qc_metrics = next(re.split(qc_pattern, line) for line in flagstat_lines if re.search(qc_pattern, line))
        (hiq, lowq) = qc_metrics[0].split(' + ')
        qc_dict[qc_key] = [int(hiq.rstrip()), int(lowq.rstrip())]

    return qc_dict

def dup_parse(dup_file):
    if not dup_file:
        return None

    lines = iter(dup_file.read().splitlines())

    for line in lines:
        if line.startswith('## METRICS CLASS'):
            headers = lines.next().rstrip('\n').lower()
            metrics = lines.next().rstrip('\n')
            break

    headers = headers.split('\t')
    metrics = metrics.split('\t')
    headers.pop(0)
    metrics.pop(0)

    dup_qc = dict(zip(headers,metrics))
    return dup_qc

def xcor_parse(xcor_file):
    if not xcor_file:
        return None

    lines = xcor_file.read().splitlines()
    line = lines[0].rstrip('\n')
    # CC_SCORE FILE format
    # Filename <tab> numReads <tab> estFragLen <tab> corr_estFragLen <tab> PhantomPeak <tab> corr_phantomPeak <tab> argmin_corr <tab> min_corr <tab> phantomPeakCoef <tab> relPhantomPeakCoef <tab> QualityTag

    headers = ['Filename','numReads','estFragLen','corr_estFragLen','PhantomPeak','corr_phantomPeak','argmin_corr','min_corr','phantomPeakCoef','relPhantomPeakCoef','QualityTag']
    metrics = line.split('\t')
    headers.pop(0)
    metrics.pop(0)

    xcor_qc = dict(zip(headers,metrics))
    return xcor_qc

def pbc_parse(pbc_file):
    if not pbc_file:
        return None

    lines = pbc_file.read().splitlines()
    line = lines[0].rstrip('\n')
    # PBC File output
    # TotalReadPairs [tab] DistinctReadPairs [tab] OneReadPair [tab] TwoReadPairs [tab] NRF=Distinct/Total [tab] PBC1=OnePair/Distinct [tab] PBC2=OnePair/TwoPair

    headers = ['TotalReadPairs','DistinctReadPairs','OneReadPair','TwoReadPairs','NRF','PBC1','PBC2']
    metrics = line.split('\t')

    pbc_qc = dict(zip(headers,metrics))
    return pbc_qc


@dxpy.entry_point('main')
def main(folder_name, key_name, assembly, noupload, force, debug):

    #accessions bams contained within the folder named folder_name/bams

    #Requires
    #. directory structure folder_name/bams/ENCSRxxxabc/ ... /basename[.anything].bam
    #. basename contains one or more ENCFF numbers from which the bam is derived
    #. bam_filename.flagstat.qc exists
    #. raw bam flagstat file exists in folder_name/raw_bams/ENCSRxxxabc/ ... /basename[.anything].flagstat.qc

    #if bam file's tags on DNAnexus already contains and ENCFF number, assume it's already accessioned and skip
    #create a fully qualified project:filename for submitted_file_name and calculate the file size
    #if an ENCFF objects exists with the same submitted_file_name, AND it has the same size, skip

    #**INFER the experiment accession number from the bam's containing folder
    #calculate the md5
    #find the raw bam's .flagstat.qc file and parse
    #find the bam's .flagstat.qc file and parse
    #**ASSUME all derived_from ENCFF's appear in the bam's filename
    #POST file object
    #Upload to AWS
    
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not folder_name.startswith('/'):
    	folder_name = '/' + folder_name
    if not folder_name.endswith('/'):
        folder_name += '/'

    try:
        project = dxpy.DXProject(dxpy.PROJECT_CONTEXT_ID)
        project_name = project.describe().get('name')
    except:
        logger.error("Failed to resolve proejct")
        project_name = ""

    bam_folder = folder_name + 'bams/'
    bams = dxpy.find_data_objects(
    	classname="file",
    	state="closed",
    	name="*.bam",
    	name_mode="glob",
    	project=dxpy.PROJECT_CONTEXT_ID,
    	folder=bam_folder,
    	recurse=True,
    	return_handler=True
	)

    authid, authpw, server = processkey(key_name)
    if not subprocess.call('which md5', shell=True):
        md5_command = 'md5 -q'
    elif not subprocess.call('which md5sum', shell=True):
        md5_command = 'md5sum'
    else:
        logger.error("Cannot find md5 or md5sum command")
        md5_command = ''

    file_mapping = []
    for bam in bams:
        already_accessioned = False
        for tag in bam.tags:
            m = re.search(r'(ENCFF\d{3}\D{3})|(TSTFF\D{6})', tag)
            if m:
                logger.info('%s appears to contain ENCODE accession number in tag %s ... skipping' %(bam.name,m.group(0)))
                already_accessioned = True
                break
        if already_accessioned:
            continue
        bam_description = bam.describe()
        submitted_file_name = project_name + ':' + '/'.join([bam.folder,bam.name])
        submitted_file_size = bam_description.get('size')
        url = urlparse.urljoin(server, 'search/?type=file&submitted_file_name=%s&format=json&frame=object' %(submitted_file_name))
        r = encoded_get(url,authid,authpw)
        try:
            r.raise_for_status()
            if r.json()['@graph']:
                for duplicate_item in r.json()['@graph']:
                    if duplicate_item.get('status')  == 'deleted':
                        logger.info("A potential duplicate file was found but its status=deleted ... proceeding")
                        duplicate_found = False
                    else:
                        logger.info("Found potential duplicate: %s" %(duplicate_item.get('accession')))
                        if submitted_file_size ==  duplicate_item.get('file_size'):
                            logger.info("%s %s: File sizes match, assuming duplicate." %(str(submitted_file_size), duplicate_item.get('file_size')))
                            duplicate_found = True
                            break
                        else:
                            logger.info("%s %s: File sizes differ, assuming new file." %(str(submitted_file_size), duplicate_item.get('file_size')))
                            duplicate_found = False
            else:
                logger.info("No duplicate ... proceeding")
                duplicate_found = False
        except:
            logger.warning('Duplicate accession check failed: %s %s' % (r.status_code, r.reason))
            logger.debug(r.text)
            duplicate_found = False

        if duplicate_found:
            if force:
                logger.info("Duplicate detected, but force=true, so continuing")
            else:
                logger.info("Duplicate detected, skipping")
                continue

        try:
            bamqc_fh = dxpy.find_one_data_object(
                classname="file",
                name='*.flagstat.qc',
                name_mode="glob",
                project=dxpy.PROJECT_CONTEXT_ID,
                folder=bam.folder,
                return_handler=True
            )
        except:
            logger.warning("Flagstat file not found ... skipping")
            continue
            bamqc_fh = None

        raw_bams_folder = str(bam.folder).replace('%sbams/' %(folder_name), '%sraw_bams/' %(folder_name), 1)
        try:
            raw_bamqc_fh = dxpy.find_one_data_object(
                classname="file",
                name='*.flagstat.qc',
                name_mode="glob",
                project=dxpy.PROJECT_CONTEXT_ID,
                folder=raw_bams_folder,
                return_handler=True
            )
        except:
            logger.warning("Raw flagstat file not found ... skipping")
            continue
            raw_bamqc_fh = None

        try:
            dup_qc_fh = dxpy.find_one_data_object(
                classname="file",
                name='*.dup.qc',
                name_mode="glob",
                project=dxpy.PROJECT_CONTEXT_ID,
                folder=bam.folder,
                return_handler=True
            )
        except:
            logger.warning("Picard duplicates QC file not found ... skipping")
            continue
            dup_qc_fh = None

        try:
            xcor_qc_fh = dxpy.find_one_data_object(
                classname="file",
                name='*.cc.qc',
                name_mode="glob",
                project=dxpy.PROJECT_CONTEXT_ID,
                folder=bam.folder,
                return_handler=True
            )
        except:
            logger.warning("Cross-correlation QC file not found ... skipping")
            continue
            xcor_qc_fh = None

        try:
            pbc_qc_fh = dxpy.find_one_data_object(
                classname="file",
                name='*.pbc.qc',
                name_mode="glob",
                project=dxpy.PROJECT_CONTEXT_ID,
                folder=bam.folder,
                return_handler=True
            )
        except:
            logger.warning("PBC QC file not found ... skipping")
            continue
            pbc_qc_fh = None

        experiment_accession = re.match('\S*(ENC\S{8})',bam.folder).group(1)
        logger.info("Downloading %s" %(bam.name))
        dxpy.download_dxfile(bam.get_id(),bam.name)
        md5_output = subprocess.check_output(' '.join([md5_command, bam.name]), shell=True)
        calculated_md5 = md5_output.partition(' ')[0].rstrip()
        encode_object = FILE_OBJ_TEMPLATE
        encode_object.update({'assembly': assembly})

        notes = {
            'filtered_qc': flagstat_parse(bamqc_fh),
            'qc': flagstat_parse(raw_bamqc_fh),
            'dup_qc': dup_parse(dup_qc_fh),
            'xcor_qc': xcor_parse(xcor_qc_fh),
            'pbc_qc': pbc_parse(pbc_qc_fh),
            'dx-id': bam_description.get('id'),
            'dx-createdBy': bam_description.get('createdBy')
        }
        encode_object.update({
            'dataset': experiment_accession,
            'notes': json.dumps(notes),
            'submitted_file_name': submitted_file_name,
            'derived_from': re.findall('(ENCFF\S{6})',bam.name),
            'file_size': submitted_file_size,
            'md5sum': calculated_md5
            })
        logger.info("Experiment accession: %s" %(experiment_accession))
        logger.debug("File metadata: %s" %(encode_object))

        url = urlparse.urljoin(server,'files')
        r = encoded_post(url, authid, authpw, encode_object)
        try:
            r.raise_for_status()
            new_file_object = r.json()['@graph'][0]
            logger.info("New accession: %s" %(new_file_object.get('accession')))
        except:
            logger.warning('POST file object failed: %s %s' % (r.status_code, r.reason))
            logger.debug(r.text)
            new_file_object = {}
            if r.status_code == 409:
                try: #cautiously add a tag with the existing accession number
                    if calculated_md5 in r.json().get('detail'):
                        url = urlparse.urljoin(server,'/search/?type=file&md5sum=%s' %(calculated_md5))
                        r = encoded_get(url,authid,authpw)
                        r.raise_for_status()
                        accessioned_file = r.json()['@graph'][0]
                        existing_accession = accessioned_file['accession']
                        bam.add_tags([existing_accession])
                        logger.info('Already accessioned.  Added %s to dxfile tags' %(existing_accession))
                except:
                    logger.info('Conflict does not appear to be md5 ... continuing')
        if noupload:
            logger.info("--noupload so skipping upload")
            upload_returncode = -1
        else:
            if new_file_object:
                creds = new_file_object['upload_credentials']
                env = os.environ.copy()
                env.update({
                    'AWS_ACCESS_KEY_ID': creds['access_key'],
                    'AWS_SECRET_ACCESS_KEY': creds['secret_key'],
                    'AWS_SECURITY_TOKEN': creds['session_token'],
                })

                logger.info("Uploading file.")
                start = time.time()
                try:
                    subprocess.check_call(['aws', 's3', 'cp', bam.name, creds['upload_url'], '--quiet'], env=env)
                except subprocess.CalledProcessError as e:
                    # The aws command returns a non-zero exit code on error.
                    logger.error("Upload failed with exit code %d" % e.returncode)
                    upload_returncode = e.returncode
                else:
                    upload_returncode = 0
                    end = time.time()
                    duration = end - start
                    logger.info("Uploaded in %.2f seconds" % duration)
                    bam.add_tags([new_file_object.get('accession')])
            else:
                upload_returncode = -1

        out_string = '\t'.join([
            experiment_accession,
            encode_object.get('submitted_file_name'),
            new_file_object.get('accession') or '',
            str(upload_returncode),
            encode_object.get('notes')
        ])
        print out_string
        file_mapping.append(out_string)

        os.remove(bam.name)

    output_log_filename = time.strftime('%m%d%y%H%M') + '-accession_log.csv'
    out_fh = dxpy.upload_string('\n'.join(file_mapping), name=output_log_filename, media_type='text/csv')
    out_fh.close()

    output = {
        "file_mapping": file_mapping,
        "outfile": dxpy.dxlink(out_fh)
    }

    return output

dxpy.run()

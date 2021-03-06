#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
:author: Robin Flume
:contact: robin.flume@rub.de
'''

import sys
import os
sys.path.insert(1, os.path.abspath('./'))
import ast
import psutil
import signal
import re
from pgf.log.logger import Logger
from pgf.analysis.schemes.plaintext_analysis import PlaintextAnalysis
from pgf.analysis.schemes.hash_analysis import HashAnalysis
from pgf.analysis.fileparser.plaintext_pure import PlaintextPure
from pgf.analysis.fileparser.hash_pure import HashPure
from pgf.analysis.fileparser.plaintext_withcount import PlaintextWithcount


class Analysis():
    ''' Class to generate an analysis scheme depending on the input format.

    :param label: Job label.
    :param pw_format: Format indicator for the inpur file (e.g. 'plaintext_pure').
    :param pw_file: Path to the file containing the leaked passwords/hashes.
    :param pid: Process ID of the guesser.
    :param analysis_interval: Interval in which the progress is written to the file.
    :param terminate_guessing: Maximum amount of candidates to be generated by the guesser before it is killed by the framework (for those guessers that do not support a 'maximum' parameter).
    :param jtr_pot_file: '.pot' file of JtR to parse for cracked candidates.
    :param output_file: Path of the output file.
    :param progress_file: Path of the progress file.
    :param plot_file: Path of the plot file.
    '''

    def __init__(self, label, pw_format, pw_file, pid, analysis_interval, terminate_guessing, jtr_pot_file, output_file, progress_file, plot_file):
        ''' Generator.
        '''
        # Initiate logger
        self.logger = Logger()
        self.logger.basicConfig('DEBUG')                     # set logger level to DEBUG
        self.label = label
        self.pw_format = pw_format
        self.pw_file = pw_file
        self.guesser_pid = ast.literal_eval(pid)
        self.analysis_interval = ast.literal_eval(analysis_interval)
        self.terminate_guessing = ast.literal_eval(terminate_guessing)
        self.jtr_pot_file = jtr_pot_file
        self.output_file = output_file
        self.progress_file = progress_file
        self.plot_file = plot_file

        # generate inputhandler depenging on input format
        self.generate_inputhandler()
        # get filetype
        self.filetype = self.inputhandler.get_filetype()
        # parse password file
        self.pws_multi, self.pw_counter, self.error_counter = self.inputhandler.parse_pw_file()
        # generate analysis scheme (which will do the actual analysis of cracked passwords
        self.generate_analysisscheme()


    def generate_inputhandler(self):
        ''' Sets the parser for the input file depending on it's content.
        '''
        if self.pw_format == 'plaintext_pure':
            self.inputhandler = PlaintextPure(self.pw_file)
        elif self.pw_format == 'hash_pure':
            self.inputhandler = HashPure(self.pw_file)
        elif self.pw_format == 'plaintext_withcount':
            self.inputhandler = PlaintextWithcount(self.pw_file)
        else:
            raise AttributeError('Unsupported file type <%s>! "plaintext_pure", "hash_pure".' % self.pw_format)


    def generate_analysisscheme(self):
        ''' Generate the analysisscheme object depending on file type of the provided password file.
        The file type is 'plaintext' for input files with the format 'plaintext_pure' or 'plaintext_colon'
        and 'hashvalues' for input files with the format 'hash_pure'.
        '''
        self.analysisscheme = None                          # init analysisscheme
        if self.filetype == 'hashvalues':
            if self.terminate_guessing is None:
                self.logger.warning("The guesser might run in endless mode as at least one of the job parameters 'terminate_guessing' is 'None'!\n")
            self.analysisscheme = HashAnalysis(self.label,
                                               self.pws_multi,
                                               self.pw_counter,
                                               self.error_counter,
                                               self.jtr_pot_file,
                                               self.output_file,
                                               self.progress_file,
                                               self.plot_file,
                                               self.analysis_interval)
        elif self.filetype == 'plaintext':
            self.analysisscheme = PlaintextAnalysis(self.label,
                                                    self.pws_multi,
                                                    self.pw_counter,
                                                    self.error_counter,
                                                    self.output_file,
                                                    self.progress_file, 
                                                    self.plot_file)
        else:
            raise AttributeError('Unsupported execution strategy!')


    def execute(self):
        ''' Start processing the generated pw candidates for plaintext input or parsing the JtR logfile for hash input.
        '''
        self.received_candidates = [None] * self.analysis_interval              # array to collect all received candidates
        self.index = 0
        self.candidate_counter = 0                                              # to count the received candidates --> kill process on certain amount
        status_line_re = re.compile('^[0-9]*g\s[0-9]*p')
        candidates_processed_re = re.compile('[0-9]*p')

        if self.filetype == 'plaintext':
            for candidate in sys.stdin:
                self.received_candidates[self.index] = candidate[:-1]           # add candidate to array (without '\n')
                self.candidate_counter += 1                                     # increment candidate counter
                self.index += 1                                                 # increment index
                self.candidate = ''                                             # reset buffer
                if (self.terminate_guessing is not None) and (self.candidate_counter == self.terminate_guessing):
                    self.logger.debug("Breaking loop at candidate_number %d" % self.candidate_counter)
                    self.kill_guesser()                                         # kill the guesser when #['terminate_guesser'] of candidates has been generated
                    break
                if self.index == self.analysis_interval:                        # when #[analysis_interval] passwords are stored in buffer, they are analyzed
                    self.analysisscheme.process_candidates(self.received_candidates) # analyze the received candidates
                    self.index = 0                                              # write next condidates from the beginning into the array
            # handle the end of candidate receiving
            self.handle_close()
        else:   # self.filetype == 'hashvalues'
            # Instead of the candidates, the status lines of JtR are processed for hashed input
            for line in sys.stdin:
#                 self.logger.debug(line)
                if not status_line_re.match(line):
                    if 'Session completed' in line:                             # all candidates cracked before amout max. guesses reached
                        self.logger.warning("Breaking loop as 'Session completed' line received by john-hash.")
                        if self.terminate_guessing is not None:
                            self.kill_guesser()
                        self.handle_close()           # process the status lines one by one
                        return                                                  # don't process last line!
                    else:
                        continue                                                # other line than status line
                else:                                                           # lines wil be such as: '736g 4008p 0:00:00:04  152.0g/s 828.0p/s 828.0c/s 885086C/s carama..marcia'
                    temp = candidates_processed_re.findall(line)[0]             # get '4008p'
                    temp = temp[:-4] + '000'                                     # remove p and replace 8 by 0 (and resepctively '4' and '12')
                    self.candidate_counter = int(temp)                          # cast '4000' to int
                    if (self.terminate_guessing is not None) and (self.candidate_counter >= self.terminate_guessing):
                        self.logger.debug("Breaking loop at candidate_number %d" % self.candidate_counter)
                        self.kill_guesser()                                     # kill the guesser when #['terminate_guesser'] of candidates has been generated
                        break
                    self.analysisscheme.process_status_line(line)               # process the status lines one by one
            self.handle_close(last_line=line)


    def kill_guesser(self):
        ''' Sends a 'SIGKILL' signal to all processes spawned by the guesser.sh file to terminate the guessing process.
        '''
        self.logger.debug("Starting kill_guesser()")

        if psutil.__version__[0] == str(1):
            # sudo apt-get install python-psutil = version 1.2.1 (Ubuntu 14.04, September 2015)
            self.logger.debug("Python psutil version 1 detected (%s)" % str(psutil.__version__))
            self.logger.debug("Calling p.get_children()")
            psversion = 1
        else:
            # sudo pip install psutil = version 3.2.0 (Ubuntu 14.04, September 2015)
            self.logger.debug("Python psutil version greater than 1 detected (%s)" % str(psutil.__version__))
            self.logger.debug("Calling p.children()")
            psversion = 3
        self.logger.debug("Parend PID to be killed: %s" % str(self.guesser_pid))
        try:
            try:
                p = psutil.Process(self.guesser_pid)
            except psutil.NoSuchProcess:
                pass
                self.logger.debug("Parent process with PID %s not found!" % str(self.guesser_pid))
            try:
                if psversion > 1:
                    children = p.children(recursive=True)
                else:
                    children = p.get_children(recursive=True)
                for child in children:
                    self.logger.debug("Killed child <%s> with pid %s" % (child.name, str(child.pid)))
                    os.kill(child.pid, signal.SIGKILL)
            except psutil.NoSuchProcess:
                pass
                self.logger.debug("No child processes found!")
            os.kill(self.guesser_pid, signal.SIGKILL)
            self.logger.debug("Killed parent process with pid %s" % str(self.guesser_pid))
        except Exception, e:
            self.logger.debug("An exception occurred while killing the guesser: <%s>" % str(e))


    def handle_close(self, last_line=None):
        ''' Handles the closing the analysis module.

        :param last_line: Last line to process by the analysisscheme.
        '''
        if self.filetype == 'plaintext':
            if self.index > 0:          # analyze the remaining candidates if there are some
                # delete the items from the previous round (already analyzed!) from the end of the array down to the fielt "self.index-1"
                for i in range((self.analysis_interval-1), (self.index-1), -1):
                    del self.received_candidates[i]
                self.analysisscheme.process_candidates(self.received_candidates)
                self.index = 0
        elif last_line is not None:
            self.analysisscheme.process_status_line(last_line)          # process last line of JtR output
        # generate the analysis results
        self.analysisscheme.gen_report()




def main():
    ''' Starts the analysis module.
    '''
    # parsing of the call arguments
    label = sys.argv[1]
    pw_format = sys.argv[2]
    pw_file = sys.argv[3]
    pid = sys.argv[4]
    analysis_interval = sys.argv[5]
    terminate_guessing = sys.argv[6]
    jtr_pot_file = sys.argv[7]
    output_file = sys.argv[8]
    progress_file = sys.argv[9]
    plot_file = sys.argv[10]


    # create an Analysis instance
    analysis = Analysis(label, pw_format, pw_file, pid, analysis_interval, terminate_guessing, jtr_pot_file, output_file, progress_file, plot_file)
    # run the analysis
    analysis.execute()


if __name__ == '__main__':
    main()

#!/usr/bin/env python

__author__    = "Andre Warnecke"
__copyright__ = "Copyright (c) 2022, André Warnecke"

#Citation for DeepCS, which is called/executed in the context of this program:
'''
@inproceedings{gu2018deepcs,
  title={Deep Code Search},
  author={Gu, Xiaodong and Zhang, Hongyu and Kim, Sunghun},
  booktitle={Proceedings of the 2018 40th International Conference on Software Engineering (ICSE 2018)},
  year={2018},
  organization={ACM}
}
'''

#Citation for CodeSearchNet dataset, of which a part was integrated into the DeepCS dataset:
'''
@article{husain2019codesearchnet,
    title={{CodeSearchNet} challenge: Evaluating the state of semantic code search},
    author={Husain, Hamel and Wu, Ho-Hsiang and Gazit, Tiferet and Allamanis, Miltiadis and Brockschmidt, Marc},
    journal={arXiv preprint arXiv:1909.09436},
    year={2019}
}
'''

import os
import io
import re
import sys
import math
import codecs
import threading
import itertools
import numpy as np
from tqdm import tqdm
from collections import Counter
from nltk.stem import PorterStemmer

from DeepCSKeras import data_loader
from DeepCSKeras import configs
from DeepCSKeras.utils import convert, revert, normalize

class IndexCreator:
    def __init__(self, args, conf = None):
        self.data_path    = args.data_path
        self.dataset_path = args.data_path + args.dataset + '/'
        self.data_params  = conf.get('data_params', dict())
        self.index_type   = args.index_type
        self.dataset      = args.dataset
        self.n_threads    = 2
        self.chunk_size   = 2000000
        self.methname_vocab = data_loader.load_pickle(self.dataset_path + conf['data_params']['vocab_methname'])
        self.token_vocab    = data_loader.load_pickle(self.dataset_path + conf['data_params']['vocab_tokens'])
        self.apiseq_vocab   = data_loader.load_pickle(self.dataset_path + conf['data_params']['vocab_apiseq'])

    def replace_synonyms(self, word):
        word = ' ' + word + ' '
        return word.replace(' read ', 'load').replace(' write', 'store').replace(' save', 'store').replace(' dump', 'store')\
        .replace(' quit', 'exit').replace(' termin ', 'exit').replace(' leav', 'exit').replace(' break ', 'exit')\
        .replace(' pop ', 'delet').replace('remov', 'delet').replace(' trim ', 'delet').replace(' strip ', 'delet')\
        .replace(' halt', 'stop').replace('restart', 'continu').replace('push ', 'add').replace('object', 'instanc')\
        .replace(' null ', 'none').replace('method', 'function').replace('concat ', 'combin')\
        .replace(' for ', 'loop').replace(' foreach ', 'loop').replace(' while ', 'loop').replace(' iterat ', 'loop')\
        .replace(' integ ', 'int').replace('tinyint ', 'int').replace(' smallint ', 'int').replace(' bigint ', 'int')\
        .replace(' shortint ', 'int').replace('longint ', 'int').replace(' byte ', 'int').replace(' short ', 'int')\
        .replace(' doubl ', 'float').replace(' long ', 'float').replace(' decim ', 'float').replace('real ', 'float')\
        .replace(' array ', '[]').replace(' arr ', '[]').replace(' fastest ', 'fast').replace(' speed ', 'fast')\
        .replace(' defin ', 'creat').replace(' declar ', 'creat').replace(' init ', 'creat').replace(' construct ', 'creat')\
        .replace(' new ', 'creat').replace(' make ', 'creat').replace(' initi ', 'creat').replace(' initid ', 'creat')\
        .replace(' boolean ', 'bool').replace('begin', 'start').replace('run ', 'execut').replace('runnabl', 'execut')\
        .replace(' enumer ', 'enum').replace(' enumerd ', 'enum').replace(' websit ', 'web')\
        .replace(' vertex ', 'node').replace(' arc ', 'edg').replace(' math ', 'calc').replace(' determin ', 'calc')\
        .replace(' should ', 'check').replace(' test ', 'check').replace(' is ', 'check').replace(' ensur ', 'check')\
        .replace(' equal ', 'compar').replace(' implement ', 'extend').replace(' whitespac ', 'space').strip()

    def load_data(self):
        assert os.path.exists(self.dataset_path + self.data_params['use_methname']), f"Method names of real data not found."
        assert os.path.exists(self.dataset_path + self.data_params['use_tokens']),   f"Tokens of real data not found."
        assert os.path.exists(self.dataset_path + self.data_params['use_apiseq']),   f"API sequences of real data not found."
        methname_indices = data_loader.load_hdf5(self.dataset_path + self.data_params['use_methname'], 0, -1)
        token_indices    = data_loader.load_hdf5(self.dataset_path + self.data_params['use_tokens'],   0, -1)
        apiseq_indices   = data_loader.load_hdf5(self.dataset_path + self.data_params['use_apiseq'],   0, -1)
        if   self.index_type == "word_indices": return methname_indices, token_indices
        elif self.index_type == "inverted_index":
            print("Translating methname, token and api sequence word indices back to natural language...   Please wait.")
            inverted_methname_vocab = dict((v, k) for k, v in self.methname_vocab.items())
            inverted_token_vocab    = dict((v, k) for k, v in self.token_vocab.items())
            inverted_apiseq_vocab   = dict((v, k) for k, v in self.apiseq_vocab.items())
            fm = lambda lst: [inverted_methname_vocab.get(i, 'UNK') for i in lst]
            ft = lambda lst: [inverted_token_vocab.get(   i, 'UNK') for i in lst]
            fa = lambda lst: [inverted_apiseq_vocab.get(  i, 'UNK') for i in lst]
            methnames = list(map(fm, methname_indices))
            tokens    = list(map(ft, token_indices))
            apiseqs   = list(map(fa, apiseq_indices))
            return methnames, tokens, apiseqs

    def process_raw_code(self):
        file  = io.open(self.dataset_path + self.data_params['use_codebase'], "r", encoding='utf8', errors='replace')
        lines = file.readlines()
        file.close()
        processed = []
        pattern0  = re.compile(r'"[^"\n]?"')
        pattern1  = re.compile(r'[^\[a-zA-Z ]+')
        pattern2  = re.compile(r'  +')
        pattern3  = re.compile(r'((?<=[a-z])[A-Z]|(?<!\A)[A-Z](?=[a-z]))')
        do_not_split = set("ArrayList,ArrayType,HashMap,heatMapTL,HttpClient,InputStream,OutputStram,ReadOnly,StringBuffer,yyyyMMdd,YYYYMMDD".split(',')) # TODO
        for line in tqdm(lines):
            line = re.sub(pattern0, '', line) # remove strings
            line = re.sub(pattern1, ' ', line) # replace all non-alphabetic characters except '[' by ' '
            line = re.sub(pattern2, ' ', line.strip()) # remove consecutive spaces
            line = line.split(' ')
            for i, word in enumerate(line):
                if word in do_not_split or word[-8:-1] == "xception":
                    word = word.lower()
                else:
                    word = re.sub(pattern3, r' \1', word).lower() # split camelcase
                line[i] = word
            processed.append(line)
        data_loader.save_pickle(self.dataset_path + self.data_params['use_processed_code'], processed)

    def save_index(self, index):
        if self.index_type == "word_indices": return
        #index_path = self.data_path + self.index_dir + '/'
        index_path = self.dataset_path
        index_file = self.index_type + '.pkl'
        #data_loader.save_index(self.index_type, index, index_path) # database
        #os.makedirs(index_path, exist_ok = True)
        #assert os.path.exists(index_path + index_file), (
        #                      f"File for index storage not found. Please create an (empty) file named {index_file} in {index_path}")
        data_loader.save_pickle(index_path + index_file, index)
        print(f"Index successfully saved to: {index_path}{index_file}")
        data_loader.save_index(self.index_type, index, index_path) # database

    def load_index(self):
        if self.index_type == "word_indices": 
            methnames, tokens, irrelevant = self.load_data()
            return methnames, tokens
        #return data_loader.load_index_counters(self.index_type) # database
        #index_path = self.data_path + self.index_dir + '/'
        index_path = self.dataset_path
        index_file = self.index_type + '.pkl'
        assert os.path.exists(index_path + index_file), f"Index file {index_file} not found at {index_path}"
        print(f"Loading index from: {index_path}{index_file}")
        return data_loader.load_pickle(index_path + index_file)
                    
    def add_to_index(self, index, lines, stopwords, n = 0):
        #print("Adding lines to the index...   Please wait.")
        #index = dict()
        """if n == 0:
            enum_lines = tqdm(enumerate(lines))
        else:
            enum_lines = enumerate(lines)"""
        if stopwords:
            porter = PorterStemmer()
            
            #for i, line in enum_lines:
            for i, line in enumerate(tqdm(lines)):
                for raw_word in line:
                #for word in line:
                    #if (length > 1 and not word in stopwords and length < 19) or word == '[':
                    for word in raw_word.split('_'):
                        length = len(word)
                        if length < 2 or length > 18 or word in stopwords: continue
                        word = porter.stem(word)
                        word = self.replace_synonyms(word)
                        if word in index:
                            #index[word].append(i)
                            index[word][i] += 1 # counts term frequence
                        else:
                            #index[word] = [i]
                            cnt = Counter()
                            cnt[i] = 1
                            index[word] = cnt
            #index_list.append(index)
            
        else:
            for i, line in enumerate(tqdm(lines)):
                for word in line:
                    if word != '[]': continue
                    if word in index:
                        #index[word].append(i)
                        index[word][i] += 1 # counts term frequence
                    else:
                        #index[word] = [i]
                        cnt = Counter()
                        cnt[i] = 1
                        index[word] = cnt

    def create_index(self, stopwords):
        if self.index_type == "word_indices": print("Nothing to be done."); return
        methnames, tokens, apiseqs = self.load_data()
        index = dict()
        #codes = data_loader.load_pickle(self.dataset_path + self.data_params['use_processed_code'])
        #number_of_code_fragments = len(codes)
        #chunk_size = math.ceil(number_of_code_fragments / self.n_threads)
        #codes = [codes[i:i + chunk_size] for i in tqdm(range(0, number_of_code_fragments, chunk_size))]
        
        index_list, threads = [], []
        if self.index_type == "inverted_index":
            print("Adding lines to the index...   Please wait.")
            self.add_to_index(index, methnames, stopwords)
            stopwords.add('new')
            self.add_to_index(index, tokens,    stopwords)
            self.add_to_index(index, apiseqs,   None)
            number_of_code_fragments = len(methnames)
            
            """for n, code in enumerate(codes):
                t = threading.Thread(target = self.add_to_index, args = (index_list, code, stopwords, n))
                threads.append(t)
            for t in threads:
                t.start()
            for t in threads:# wait until all sub-threads finish
                t.join()"""
            #self.add_to_index(index, codes, stopwords)
            #del codes
            
            """index = index_list[0]
            for i in tqdm(range(1, len(index_list))):
                ind = index_list[i]
                for word in list(ind.keys()):
                    if word in index:
                        index[word] += ind[word]
                    else:
                        index[word] = ind[word]"""
            
            for line_counter in tqdm(index.values()):
                lines = list(line_counter.keys()) # deduplicated list of code fragments
                idf   = math.log10(number_of_code_fragments / len(lines)) # inverse document frequence = log10(N / df)
                for line_nr in lines: # replace currently stored term frequence by tf-idf:
                    #line_counter[line_nr] = idf * math.log(1 + line_counter[line_nr]) # tf-idf = idf * log(1 + tf)
                    line_counter[line_nr] = idf * math.log10(1 + line_counter[line_nr]) # tf-idf = idf * log10(1 + tf)
            for word in index.keys():
                index[word] = Counter(dict(sorted(index[word].items(), key=lambda x: (-x[1], x[0]))))
                #print(itertools.islice(index[word].values(), 100))
        
        self.save_index(index)

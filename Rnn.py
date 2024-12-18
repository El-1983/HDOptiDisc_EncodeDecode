import os
import argparse
import shutil

import torch
import torch.nn as nn
import torchvision
import torch.optim as optim
import torch.nn.functional as F
import numpy as np
from numpy import linalg as LA
import math
import sys
import datetime
np.set_printoptions(threshold=sys.maxsize)

from lib import Constant
from lib.utils import codeword_threshold, find_index

parser = argparse.ArgumentParser()

parser.add_argument('-learning_rate', type = float, default=0.001)
parser.add_argument('-momentum', type=float, default=0.9)
parser.add_argument('-num_epoch', type=int, default=400)
parser.add_argument('-epoch_start', type=int, default=0)
parser.add_argument('-num_batch', type=int, default=20)
parser.add_argument('-weight_decay', type=float, default=0.0001)
parser.add_argument('-eval_freq', type=int, default=5)
parser.add_argument('-eval_start', type=int, default=200)
parser.add_argument('-print_freq_ep', type=int, default=5)

parser.add_argument('-result', type=str, default='result.txt')
parser.add_argument('-checkpoint', type=str, default='checkpoint.pth.tar')
parser.add_argument('-resume', default=None, type=str, metavar='PATH', 
                    help='path to latest checkpoint (default: none)')

parser.add_argument('-prob_start', type=float, default=0.1)
parser.add_argument('-prob_up', type=float, default=0.01)
parser.add_argument('-prob_step_ep', type=int, default=5)

parser.add_argument('-eval_info_length', type=int, default=1000000)
parser.add_argument('-dummy_length_start', type=int, default=5)
parser.add_argument('-dummy_length_end', type=int, default=5)
parser.add_argument('-eval_length', type=int, default=10)
parser.add_argument('-overlap_length', type=int, default=20)

parser.add_argument('-batch_size_snr_train_1', type=int, default=30)
parser.add_argument('-batch_size_snr_train_2', type=int, default=30)
parser.add_argument('-batch_size_snr_validate_1', type=int, default=600)
parser.add_argument('-batch_size_snr_validate_2', type=int, default=600)
parser.add_argument('-snr_start', type=float, default=8.5)
parser.add_argument('-snr_stop', type=float, default=10.5)
parser.add_argument('-snr_step', type=float, default=0.5)

parser.add_argument('-input_size', type=int, default=5)
parser.add_argument('-rnn_input_size', type=int, default=5)
parser.add_argument('-rnn_hidden_size', type=int, default=50)
parser.add_argument('-output_size', type=int, default=1)
parser.add_argument('-rnn_layer', type=int, default=4)
parser.add_argument('-rnn_dropout_ratio', type=float, default=0)

parser.add_argument('-scaling_para', type=float, default=0.25)
parser.add_argument('-PW50_1', type=float, default=2.54)
parser.add_argument('-PW50_2', type=float, default=2.88)
parser.add_argument('-T', type=float, default=1)
parser.add_argument('-tap_lor_num', type=int, default=41)
parser.add_argument('-tap_isi_num', type=int, default=21)
parser.add_argument('-tap_pre_num', type=int, default=4)

def main():
    global args
    args = parser.parse_known_args()[0]
    
    # cuda device
    os.environ['CUDA_VISIBLE_DEVICES'] = "0"
    if torch.cuda.is_available():
        device = torch.device("cuda")
        
    # write the results
    dir_name = './output_' + datetime.datetime.strftime(datetime.datetime.now(), 
                                                        '%Y-%m-%d_%H:%M:%S') + '/'
    os.mkdir(dir_name)
    result_path = dir_name + args.result
    result = open(result_path, 'w+')
    
    # data loader
    (encoder_dict, channel_dict, dummy_dict_start, 
     dummy_dict_end, dummy_dict_end_eval, dummy_input_end) = Constant()
    data_class = Dataset(args, device, encoder_dict, channel_dict, 
                         dummy_dict_start, dummy_dict_end, dummy_input_end)
    data_eval_acgn_1, data_eval_acgn_2, label_eval = (data_class.data_generation_eval(8.5))
    
    snr_point = int((args.snr_stop-args.snr_start)/args.snr_step+1)
    
    # model
    model = Network(args, device).to(device)
    
    # criterion and optimizer
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), 
                                 lr=args.learning_rate, 
                                 eps=1e-08, 
                                 weight_decay=args.weight_decay)
    
    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume)
            args.epoch_start = checkpoint['epoch']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))

     
    # train and validation
    
    prob_start_ori = 0.1
    if args.epoch_start > args.eval_start:
        prob_start = 0.5
    else:
        prob_start = prob_start_ori + (args.prob_up * 
                                       (args.epoch_start // args.prob_step_ep))
    
    prob_end = 0.5
    prob_step = int((prob_end - prob_start_ori) / args.prob_up)
    prob_ep_list = list(range(prob_step*args.prob_step_ep, 0, -args.prob_step_ep))
    prob = prob_start
    for epoch in range(args.epoch_start, args.num_epoch):
        
        # increase the probability each 10 epochs
        if epoch in prob_ep_list:
            prob += args.prob_up
        
        # train and validate
        train_loss = train(data_class, prob, model, optimizer, epoch, device)
        valid_loss, ber_acgn_1, ber_acgn_2 = validate(data_class, prob, channel_dict, dummy_dict_start, 
                                                      dummy_dict_end_eval, model, epoch, device)
        
        result.write('epoch %d \n' % epoch)
        result.write('information prob.'+str(prob)+'\n')
        result.write('Train loss:'+ str(train_loss)+'\n')
        result.write('Validation loss:'+ str(valid_loss)+'\n')
        if (epoch >= args.eval_start and epoch % args.eval_freq == 0):
            result.write('-----[acgn] [PW50_1] SNR[dB]:'+str(ber_acgn_1)+'\n')
            result.write('-----[acgn] [PW50_2] SNR[dB]:'+str(ber_acgn_2)+'\n')
        else:
            result.write('-----:no evaluation'+'\n')
        result.write('\n')
        
        torch.save({
            'epoch': epoch+1,
            'arch': 'rnn',
            'state_dict': model.state_dict(),
            'optimizer': optimizer.state_dict(),
        }, args.checkpoint)

## Dataset: generate dataset for neural network
class Dataset(object):
    def __init__(self, args, device, encoder_machine, channel_machine, 
                 dummy_dict_start, dummy_dict_end, dummy_input_end):
        self.args = args
        self.device = device
        
        self.encoder_machine = encoder_machine
        self.num_state = len(self.encoder_machine)
        self.num_input_sym_enc = self.encoder_machine[1]['input'].shape[1]
        self.num_out_sym = self.encoder_machine[1]['output'].shape[1]
        self.code_rate = self.num_input_sym_enc / self.num_out_sym
        
        self.channel_machine = channel_machine
        self.ini_state_channel = self.channel_machine['ini_state']
        self.num_input_sym_channel = int(self.channel_machine['in_out'].shape[1]/2)
        
        self.dummy_dict_start = dummy_dict_start
        self.dummy_dict_end = dummy_dict_end
        self.dummy_input_end = dummy_input_end
        
        self.isi_coef_1 = self.isi_coef_derivation(args.PW50_1)
        self.isi_coef_2 = self.isi_coef_derivation(args.PW50_2)
        
        self.lor_di_coef_1 = self.lorentzian_di_channel(args.PW50_1)
        self.lor_di_coef_2 = self.lorentzian_di_channel(args.PW50_2)
        
        print('The ISI coefficient of PW50_1 (PW50={}) is\n'.format(args.PW50_1))
        print(self.isi_coef_1)
        print('The ISI coefficient of PW50_2 (PW50={}) is\n'.format(args.PW50_2))
        print(self.isi_coef_2)
        print('The dipulse Lorentzian coefficient of PW50_1 (PW50={}) is\n'.format(args.PW50_1))
        print(self.lor_di_coef_1)
        print('The dipulse Lorentzian coefficient of PW50_2 (PW50={}) is\n'.format(args.PW50_2))
        print(self.lor_di_coef_2)

    def isi_coef_derivation(self, PW50):
        # coefficients for PR equalizer
        isi_alpha = [1, 3, 3, 1]
        isi_alpha_len = len(isi_alpha)
        tap_isi_num_side = int((args.tap_isi_num - 1) / 2)
        isi_coef_ori = np.zeros((1, args.tap_isi_num))
        for k in range(-tap_isi_num_side, tap_isi_num_side+1):
            coef_tmp = 0
            for i in range(isi_alpha_len):
                value_tmp = (isi_alpha[i] * (((-1)**i) * math.exp(math.pi*PW50/2) * math.cos(k*math.pi) 
                                             - PW50/2) / ((PW50/2)**2 + (k-i)**2))
                coef_tmp += value_tmp
            isi_coef_ori[0, k] = coef_tmp / (math.pi**2)
        
        isi_coef_ori = isi_coef_ori / np.sqrt(10)
        
        isi_coef = np.append(isi_coef_ori[:, -tap_isi_num_side:], 
                                  isi_coef_ori[:, :tap_isi_num_side+1], axis=1)
        
        return isi_coef
    
    def data_generation_train(self, prob, bt_size_snr_1, bt_size_snr_2):
        '''
        training/testing data(with sliding window) and label
        output: float torch tensor (device)
        '''
        
        bt_size_1 = int(((args.snr_stop-args.snr_start)/
                         args.snr_step+1)*bt_size_snr_1)
        bt_size_2 = int(((args.snr_stop-args.snr_start)/
                         args.snr_step+1)*bt_size_snr_2)
        
        block_length = (args.dummy_length_start + args.eval_length + 
                        args.overlap_length + args.dummy_length_end)
        info_length = math.ceil((block_length - args.dummy_length_end)
                                /self.num_out_sym)*self.num_input_sym_enc
        
        info_1 = np.random.choice(np.arange(0, 2), size = (bt_size_snr_1, info_length), 
                                  p=[1-prob, prob])
        
        info_2 = np.random.choice(np.arange(0, 2), size = (bt_size_snr_2, info_length), 
                                  p=[1-prob, prob])
        
        data_bt_1, label_bt_1 = (np.zeros((bt_size_1, block_length)), 
                                 np.zeros((bt_size_1, args.eval_length+args.overlap_length)))
        data_bt_2, label_bt_2 = (np.zeros((bt_size_2, block_length)), 
                                 np.zeros((bt_size_2, args.eval_length+args.overlap_length)))
                
        for i in range(bt_size_snr_1):
            codeword = (self.precoding(self.encoder_constrain(info_1[i : i+1, :]))
                        [:, :block_length - args.dummy_length_end])
            codeword_isi, state = self.e2pr4_channel(codeword)
            codeword_isi_end = np.concatenate((codeword_isi, 
                                               self.dummy_dict_end[state]), axis=1)
            
            for idx in np.arange(0, (args.snr_stop-args.snr_start)/args.snr_step+1):
                label_bt_1[int(idx*bt_size_snr_1+i) : int(idx*bt_size_snr_1+i+1), 
                           :] = (codeword[:, args.dummy_length_start:
                                          block_length-args.dummy_length_end])
                
                codeword_noisy_tmp = self.acgn_mis(np.concatenate((codeword, self.dummy_input_end[state]), 
                                                                  axis=1), 
                                                   args.snr_start+idx*args.snr_step, 
                                                   self.isi_coef_1, self.lor_di_coef_1)
                
                codeword_noisy = codeword_noisy_tmp[:, args.dummy_length_start:]
                
                
                data_bt_1[int(idx*bt_size_snr_1+i) : int(idx*bt_size_snr_1+i+1), 
                          :args.dummy_length_start] = codeword_isi_end[:, :args.dummy_length_start]
                data_bt_1[int(idx*bt_size_snr_1+i) : int(idx*bt_size_snr_1+i+1), 
                          args.dummy_length_start:] = codeword_noisy
        
        for i in range(bt_size_snr_2):
            codeword = (self.precoding(self.encoder_constrain(info_2[i : i+1, :]))
                        [:, :block_length - args.dummy_length_end])
            codeword_isi, state = self.e2pr4_channel(codeword)
            codeword_isi_end = np.concatenate((codeword_isi, 
                                               self.dummy_dict_end[state]), axis=1)
            
            for idx in np.arange(0, (args.snr_stop-args.snr_start)/args.snr_step+1):
                label_bt_2[int(idx*bt_size_snr_2+i) : int(idx*bt_size_snr_2+i+1), 
                           :] = (codeword[:, args.dummy_length_start:
                                          block_length-args.dummy_length_end])
                
                codeword_noisy_tmp = self.acgn_mis(np.concatenate((codeword, self.dummy_input_end[state]), 
                                                                  axis=1), 
                                                   args.snr_start+idx*args.snr_step, 
                                                   self.isi_coef_2, self.lor_di_coef_2)
                
                codeword_noisy = codeword_noisy_tmp[:, args.dummy_length_start:]
                
                data_bt_2[int(idx*bt_size_snr_2+i) : int(idx*bt_size_snr_2+i+1), 
                          :args.dummy_length_start] = codeword_isi_end[:, :args.dummy_length_start]
                data_bt_2[int(idx*bt_size_snr_2+i) : int(idx*bt_size_snr_2+i+1), 
                          args.dummy_length_start:] = codeword_noisy
        
        data_bt = np.append(data_bt_1, data_bt_2, axis=0)
        label_bt = np.append(label_bt_1, label_bt_2, axis=0)
        
        data_bt = self.sliding_shape(torch.from_numpy(data_bt).float().to(self.device))
        label_bt = (torch.from_numpy(label_bt).float()).to(self.device)
        
        return data_bt, label_bt
    
    def data_generation_eval(self, snr):
        '''
        evaluation data(without sliding window) and label
        output: float torch tensor data_eval, numpy array label_eval
        '''
        
        info = np.random.randint(2, size = (1, args.eval_info_length))
        codeword = self.precoding(self.encoder_constrain(info))
        codeword_isi, _ = self.e2pr4_channel(codeword)
        
        codeword_acgn_mis_1 = self.acgn_mis(codeword, snr, self.isi_coef_1, self.lor_di_coef_1)
        codeword_acgn_mis_2 = self.acgn_mis(codeword, snr, self.isi_coef_2, self.lor_di_coef_2)
        
        data_eval_acgn_mis_1 = torch.from_numpy(codeword_acgn_mis_1).float().to(self.device)
        data_eval_acgn_mis_2 = torch.from_numpy(codeword_acgn_mis_2).float().to(self.device)
        label_eval = codeword
        
        return data_eval_acgn_mis_1, data_eval_acgn_mis_2, label_eval
        
    def sliding_shape(self, x):
        '''
        Input: (1, length) torch tensor
        Output: (input_size, length) torch tensor
        Mapping: sliding window for each time step
        '''
        
        batch_size, time_step = x.shape
        zero_padding_len = args.input_size - 1
        x = torch.cat(((torch.zeros((batch_size, zero_padding_len))).to(self.device), x), 1)
        y = torch.zeros(batch_size, time_step, args.input_size)
        for bt in range(batch_size):
            for time in range(time_step):
                y[bt, time, :] = x[bt, time:time+args.input_size]
        return y.float().to(self.device)
            
    def encoder_constrain(self, info):
        '''
        Input: (1, length) array
        Output: (1, length / rate) array
        Mapping: Encoder (Markov Chain)
        '''
        
        info_len = np.size(info, 1)
        codeword = np.zeros((1, int(info_len/self.code_rate)))
        
        state = np.random.randint(low=1, high=self.num_state+1, size=1)[0]
        for i in range(0, info_len, self.num_input_sym_enc):
            # start symbol and state
            idx = int(i / self.num_input_sym_enc)
            input_sym = info[:, i:i+self.num_input_sym_enc][0]
            # input idx
            idx_in = find_index(self.encoder_machine[state]['input'], input_sym)
            # output sym and next state
            output_sym = self.encoder_machine[state]['output'][idx_in, :]
            state = self.encoder_machine[state]['next_state'][idx_in, 0]
            codeword[:, self.num_out_sym*idx : self.num_out_sym*(idx+1)] = output_sym
        
        return codeword.astype(int)
    
    def precoding(self, z):
        '''
        Input: (1, length) array
        Output: (1, length) array
        Mapping: x = (1 / 1 + D) z (mod 2)
        x_{-1} = 0
        '''
        
        length = np.size(z, 1)
        x = np.zeros((1, length))
        x[0, 0] = z[0, 0]
        for i in range(1, length):
            x[0, i] = x[0, i-1] + z[0, i]
        return x % 2
    
    def e2pr4_channel(self, x):
        '''
        Input: (1, length) array
        Output: (1, length) array, ending state
        Mapping: channel state machine
        '''
            
        length = x.shape[1]
        y = np.zeros((1, length))
        
        # Memory channel
        state = self.ini_state_channel
        for i in range(0, length, self.num_input_sym_channel):
            set_in = np.where(self.channel_machine['state_machine'][:, 0]==state)[0]
            idx_in = set_in[np.where(self.channel_machine['in_out'][set_in, 0]==x[:, i])[0]]
            y[:, i] = self.channel_machine['in_out'][idx_in, 1]
            state = self.channel_machine['state_machine'][idx_in, 1]
            
        return y, state[0]
    
    def lorentzian_di_channel(self, PW50):
        '''
        g(t)=\frac{1}{1+(2t/PW50)^{2}}
        h(t)=g(t)-g(t-T)
        '''
        
        tap_lor_num_side = int((args.tap_lor_num - 1) / 2)
        lorentzian_coef_ori = np.zeros((1, args.tap_lor_num))
        
        for i in range(-tap_lor_num_side, tap_lor_num_side+1):
            lorentzian_coef_ori[0, i] = 1 / (1 + (2*i/PW50)**2)
        
        lorentzian_coef = np.append(lorentzian_coef_ori[:, -tap_lor_num_side:], 
                                    lorentzian_coef_ori[:, :tap_lor_num_side+1], axis=1)
        
        lorentzian_shift_coef = np.append(np.zeros((1, 1)), lorentzian_coef[:, :-1], axis=1)
        lorentzian_di_coef = lorentzian_coef - lorentzian_shift_coef
        
        return lorentzian_di_coef
    
    def awgn(self, x, snr):
        sigma = np.sqrt(args.scaling_para * 10 ** (- snr * 1.0 / 10))
        return x + sigma * np.random.normal(0, 1, x.shape)
    
    def acgn_mis(self, codeword, snr, isi_coef, lor_di_coef):
        scaling_para_isi = (1 / LA.norm(isi_coef, 2)) ** 2
        sigma = np.sqrt(args.scaling_para * scaling_para_isi * 10 ** (- snr * 1.0 / 10))
        noise_white = sigma * np.random.normal(0, 1, codeword.shape)
        
        tap_isi_num_side = int((args.tap_isi_num - 1) / 2)
        tap_lor_num_side = int((args.tap_lor_num - 1) / 2)
        
        noise_color = (np.convolve(isi_coef[0, :], noise_white[0, :])
                       [tap_isi_num_side:-tap_isi_num_side].reshape(codeword.shape))
        
        lor = (np.convolve(lor_di_coef[0, :], codeword[0, :])
               [tap_lor_num_side:-tap_lor_num_side].reshape(codeword.shape))
        
        output_isi = (np.convolve(isi_coef[0, :], lor[0, :])
                      [tap_isi_num_side:-tap_isi_num_side].reshape(codeword.shape))
        
        output_mis = (np.convolve(isi_coef[0, :], lor[0, :]+noise_white[0, :])
                      [tap_isi_num_side:-tap_isi_num_side].reshape(codeword.shape))
        
        return output_mis
    
class Network(nn.Module):
    def __init__(self, args, device):
        super(Network, self).__init__()
        
        self.args = args
        self.device = device
        self.time_step = (args.dummy_length_start + args.eval_length 
                          + args.overlap_length + args.dummy_length_end)
        self.fc_length = args.eval_length + args.overlap_length
        self.dec_input = torch.nn.Linear(args.input_size, 
                                         args.rnn_input_size)
        self.dec_rnn = torch.nn.GRU(args.rnn_input_size, 
                                    args.rnn_hidden_size, 
                                    args.rnn_layer, 
                                    bias=True, 
                                    batch_first=True,
                                    dropout=args.rnn_dropout_ratio, 
                                    bidirectional=True)
        
        self.dec_output = torch.nn.Linear(2*args.rnn_hidden_size, args.output_size)
        
    def forward(self, x):
        batch_size = x.size(0)
        dec = torch.zeros(batch_size, self.fc_length, 
                          args.output_size).to(self.device)
        
        x = self.dec_input(x)
        y, _  = self.dec_rnn(x)
        y_dec = y[:, args.dummy_length_start : 
                  self.time_step-args.dummy_length_end, :]

        dec = torch.sigmoid(self.dec_output(y_dec))
        
        return torch.squeeze(dec, 2)
    

def train(data_class, prob, model, optimizer, epoch, device):

    # switch to train mode
    model.train()
    
    train_loss = 0
    for batch_idx in range(args.num_batch):
        # data
        data_train, label_train = (data_class.data_generation_train
                                   (prob, args.batch_size_snr_train_1, 
                                    args.batch_size_snr_train_2))
        
        # network
        optimizer.zero_grad()
        output = model(data_train)
        loss = loss_func(output, label_train)
        
        # compute gradient and do gradient step
        loss.backward()
        optimizer.step()
        train_loss += loss.item()
        
        # print
        if (epoch % args.print_freq_ep == 0 and 
            (batch_idx+1) % args.num_batch == 0):
            avg_loss = train_loss / args.num_batch
            print('Train Epoch: {} (w.p. {:.2f}) - Loss: {:.6f}, Avg Loss: {:.6f}'
                  .format(epoch+1, prob, train_loss, avg_loss))
    
    return loss.item()
            

def validate(data_class, prob, channel_dict, dummy_dict_start, 
             dummy_dict_end_eval, model, epoch, device):
    
    np.random.seed(12345)
    # switch to evaluate mode
    model.eval()
    
    # data
    data_val, label_val = (data_class.data_generation_train
                           (prob, args.batch_size_snr_validate_1, 
                            args.batch_size_snr_train_2))
        
    # network
    with torch.no_grad():
        output = model(data_val)
        valid_loss = loss_func(output, label_val)
    
    if epoch % args.print_freq_ep == 0:
        print('Validation Epoch: {} - Loss: {:.6f}'.
              format(epoch+1, valid_loss.item()))
    
    # evaluation for a very long sequence
    ber_acgn_1 = np.ones((1, int((args.snr_stop-args.snr_start)/args.snr_step+1)))
    ber_acgn_2 = np.ones((1, int((args.snr_stop-args.snr_start)/args.snr_step+1)))
    
    if (epoch >= args.eval_start) & (epoch % args.eval_freq == 0):
        for idx in np.arange(0, int((args.snr_stop-args.snr_start)/args.snr_step+1)):
            data_eval_acgn_1, data_eval_acgn_2, label_eval = (data_class.data_generation_eval
                                                              (args.snr_start+idx*args.snr_step))
            dec_acgn_1 = evaluation(data_eval_acgn_1, dummy_dict_start, dummy_dict_end_eval, 
                                    channel_dict, data_class, model, device)
            dec_acgn_2 = evaluation(data_eval_acgn_2, dummy_dict_start, dummy_dict_end_eval, 
                                    channel_dict, data_class, model, device)
            ber_acgn_1[0, idx] = (np.sum(np.abs(dec_acgn_1.cpu().numpy() - label_eval))
                                  /label_eval.shape[1])
            ber_acgn_2[0, idx] = (np.sum(np.abs(dec_acgn_2.cpu().numpy() - label_eval))
                                  /label_eval.shape[1])
        print('Validation Epoch: {} - [realistic] PW50_1 ber: {}'.format(epoch+1, ber_acgn_1))
        print('Validation Epoch: {} - [realistic] PW50_2 ber: {}'.format(epoch+1, ber_acgn_2))
        
    
    return valid_loss.item(), ber_acgn_1, ber_acgn_2
        
def evaluation(x, dummy_dict_start, dummy_dict_end_eval,
               channel_dict, data_class, model, device):
    # paras
    truncation_len = args.eval_length + args.overlap_length
    state_num = channel_dict['state_label'].shape[1]
    x_len = x.shape[1]
    
    # add dummy bits to x
    tail_bit = (torch.zeros((1, args.overlap_length))).to(device)
    x = torch.cat((x, tail_bit), 1)
    
    # dummy ending values for evaluation
    dummy_dict_end_eval = dummy_dict_end_eval.to(device)
    
    state = 0
    dec = torch.zeros((1, 0)).float().to(device)
    
    for idx in range(0, x_len, args.eval_length):
        # decode one truncation block
        truncation = x[:, idx : idx+truncation_len]
        truncation_block = torch.cat((torch.cat((dummy_dict_start[state].
                                                 to(device), truncation), 1), 
                                      dummy_dict_end_eval), 1)
        truncation_in = data_class.sliding_shape(truncation_block)
        with torch.no_grad():
            dec_block = codeword_threshold(model(truncation_in)
                                           [:, :args.eval_length])
        # concatenate the decoding codeword
        dec = torch.cat((dec, dec_block), 1)
        
        # find the initial state in block
        state_label = dec[:, -state_num:]
        state = find_index(channel_dict['state_label'], state_label[0])

        if state == None:
            state = 0
        
    return dec

def loss_func(output, label):
    
    return F.binary_cross_entropy(output, label).cuda()

def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
    torch.save(state, filename)
        
if __name__ == '__main__':
    main()
# Copyright (c) 2019, NVIDIA Corporation. All rights reserved.
#
# This work is made available
# under the Nvidia Source Code License (1-way Commercial).
# To view a copy of this license, visit
# https://nvlabs.github.io/few-shot-vid2vid/License.txt
import sys
import numpy as np
import torch
from tqdm import tqdm

from options.train_options import TrainOptions
from data.data_loader import CreateDataLoader
from models.models import create_model
from models.loss_collector import loss_backward
from models.trainer import Trainer
from util.distributed import init_dist
from util.distributed import master_only_print as print

import pdb

import warnings
warnings.simplefilter('ignore')

def train():
    opt = TrainOptions().parse()    
    if opt.distributed:
        init_dist()
        opt.batchSize = opt.batchSize // len(opt.gpu_ids)    

    ### setup dataset
    data_loader = CreateDataLoader(opt)
    dataset = data_loader.load_data()

    ### setup trainer    
    trainer = Trainer(opt, data_loader) 

    ### setup models
    model, flowNet = create_model(opt, trainer.start_epoch)
    flow_gt = conf_gt = [None] * 2       
    
    ref_idx_fix = torch.zeros([opt.batchSize])
    for epoch in tqdm(range(trainer.start_epoch, opt.niter + opt.niter_decay + 1)):
        trainer.start_of_epoch(epoch, model, data_loader)
        n_frames_total, n_frames_load = data_loader.dataset.n_frames_total, opt.n_frames_per_gpu
        for idx, data in enumerate(tqdm(dataset), start=trainer.epoch_iter):
            trainer.start_of_iter()            

            if not opt.no_flow_gt: 
                data_list = [data['tgt_image'], data['cropped_images'], data['warping_ref'], data['ani_image']]
                flow_gt, conf_gt = flowNet(data_list, epoch)
            data_list = [data['tgt_label'], data['tgt_image'], flow_gt, conf_gt]
            data_ref_list = [data['ref_label'], data['ref_image']]
            data_prev = [None, None]
            data_ani = [data['warping_ref_lmark'], data['warping_ref'], data['ani_lmark'], data['ani_image']]

            ############## Forward Pass ######################
            for t in range(0, n_frames_total, n_frames_load):
                
                data_list_t = get_data_t(data_list, n_frames_load, t) + data_ref_list + \
                              get_data_t(data_ani, n_frames_load, t) + data_prev
                                
                g_losses, generated, data_prev, ref_idx = model(data_list_t, save_images=trainer.save, mode='generator', ref_idx_fix=ref_idx_fix)
                g_losses = loss_backward(opt, g_losses, model.module.optimizer_G)

                d_losses, _ = model(data_list_t, mode='discriminator', ref_idx_fix=ref_idx_fix)
                d_losses = loss_backward(opt, d_losses, model.module.optimizer_D)
                        
            loss_dict = dict(zip(model.module.lossCollector.loss_names, g_losses + d_losses))     

            output_data_list = generated + data_list + [data['ref_label'], data['ref_image']] + data_ani + [data['cropped_lmarks'], data['cropped_images']]

            if trainer.end_of_iter(loss_dict, output_data_list, model):
                break        

        trainer.end_of_epoch(model)

def get_data_t(data, n_frames_load, t):
    if data is None: return None
    if type(data) == list:
        return [get_data_t(d, n_frames_load, t) for d in data]
    return data[:,t:t+n_frames_load]

if __name__ == "__main__":
   train()
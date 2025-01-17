import config, optimizers
import torch
import torch.distributed as dist

from torch.optim import Optimizer
from torch import nn, Tensor
from typing import Tuple

from model.models import gpt2_get_params_for_weight_decay_optimization
from apex.optimizers import FusedAdam as Adam

from apex.normalization.fused_layer_norm import FusedLayerNorm as LayerNorm

from torch.nn.parallel import DistributedDataParallel as NativeDDP

from apex.parallel import DistributedDataParallel as APEX_DDP

from model.fp16 import FP16_Module
from model.fp16 import FP16_Optimizer

def convert_model(model: nn.Module) -> nn.Module:
    state_dict = model.state_dict()
    state_dict = remap_attn_parameters(state_dict)

    for i in range(config.num_layers):
        model.transformer.layers[i].input_layernorm = LayerNorm(config.hidden_size, config.layernorm_epsilon)
        model.transformer.layers[i].post_attention_layernorm = LayerNorm(config.hidden_size, config.layernorm_epsilon)
    model.transformer.final_layernorm = LayerNorm(config.hidden_size, config.layernorm_epsilon)

    model.load_state_dict(state_dict, strict=True)
    return model

def remap_attn_parameters(model_dict):
    return model_dict

def create_optimizer(config, model: nn.Module) -> Optimizer:

    #named_params = list(model.named_parameters())
    #no_decay = ['bias', 'gamma', 'beta', 'LayerNorm']

    #optimizer_grouped_parameters = [
    #    {'params': [p for n, p in named_params if not any(nd in n for nd in no_decay)],
    #     'weight_decay': config.weight_decay_rate},
    #    {'params': [p for n, p in named_params if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}]

    #optimizer = optimizers.create_optimizer(name='adamw', params=optimizer_grouped_parameters, config=config)
    
    param_groups = gpt2_get_params_for_weight_decay_optimization(model)
    optimizer = Adam(param_groups,
    lr = config.learning_rate, weight_decay = config.weight_decay_rate)
    
    return optimizer

def model_to_fp16(model: nn.Module, optimizer: Optimizer) -> Tuple[nn.Module, Optimizer]:
    model = FP16_Module(model)
    args = config
    optimizer = FP16_Optimizer(optimizer,
                        static_loss_scale=args.loss_scale,
                        dynamic_loss_scale=args.dynamic_loss_scale,
                        dynamic_loss_args={
                            'scale_window': args.loss_scale_window,
                            'min_scale': args.min_scale,
                            'delayed_shift': args.hysteresis})
    
    return model, optimizer

def model_to_ddp(model: nn.Module) -> nn.Module:
    use_ddp = dist.is_initialized()

    if use_ddp:
        if config.ddp_type == 'native':
            model = NativeDDP(model,
                                device_ids=[config.local_rank],
                                bucket_cap_mb=100,
                                gradient_as_bucket_view=config.use_gradient_as_bucket_view)
        elif config.ddp_type == 'apex':
            model = APEX_DDP(model,
                                message_size=250000000,
                                delay_allreduce=True,
                                gradient_predivide_factor=torch.distributed.get_world_size())
        else:
            assert False, "Invalid DDP type"
    return model

def create_grad_scaler():
    return None

def backward(step: int, loss: Tensor, optimizer, **kwarg):
    # loss.backward()
    # optimizer.step()
    # optimizer.zero_grad()
    # return
    if config.fp16:
            optimizer.backward(loss)
    else:
        loss.backward()

    if step % config.gradient_accumulation_steps == 0:
        optimizer.step()
        optimizer.zero_grad()


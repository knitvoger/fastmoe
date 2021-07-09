r"""
Adaption to act as the MLP layer using an MoE MLP layer in transformer.
"""
import torch
import torch.nn as nn
from .gates import NaiveGate
from .layers import FMoE, FMoELinear, FMoEConv


class _LinearExpert(nn.Module):
    r"""
    An expert using 2 FMoELinear modules to speed up the computation of experts
    within one worker.
    """

    def __init__(self, num_expert, d_model, d_hidden, activation, rank=0):
        super().__init__()
        self.htoh4 = FMoELinear(num_expert, d_model, d_hidden, bias=True, rank=rank)
        self.h4toh = FMoELinear(num_expert, d_hidden, d_model, bias=True, rank=rank)
        self.activation = activation

    def forward(self, inp, fwd_expert_count):
        r"""
        First expand input to 4h (the hidden size is variable, but is called h4
        for convenience). Then perform activation. Finally shirink back to h.
        """
        x = self.htoh4(inp, fwd_expert_count)
        x = self.activation(x)
        x = self.h4toh(x, fwd_expert_count)
        return x


class _ConvExpert(nn.Module):
    r"""
    An expert using 2 FMoELinear modules to speed up the computation of experts
    within one worker.
    """

    def __init__(self, num_expert, d_model, d_hidden, kernel_size, dilation, activation, rank=0):
        super().__init__()
        self.htoh4 = FMoEConv(num_expert, d_model, d_hidden, bias=True, kernel_size=kernel_size, dilation=dilation, rank=rank)
        self.h4toh = FMoEConv(num_expert, d_hidden, d_model, bias=True, kernel_size=kernel_size, dilation=dilation, rank=rank)
        self.activation = activation
        self.num_expert = num_expert
        self.d_model = d_model
        self.d_hidden = d_hidden

    def forward(self, inp, fwd_expert_count):
        r"""
        First expand input to 4h (the hidden size is variable, but is called h4
        for convenience). Then perform activation. Finally shirink back to h.
        """
        x = self.htoh4(inp, fwd_expert_count)
        x = self.activation(x)
        x = self.h4toh(x, fwd_expert_count)
        return x


class FMoETransformerMLP(FMoE):
    r"""
    A complete MoE MLP module in a Transformer block.
    * `activation` is the activation function to be used in MLP in each expert.
    * `d_hidden` is the dimension of the MLP layer.
    """

    def __init__(
        self,
        num_expert=32,
        d_model=1024,
        d_hidden=4096,
        world_size=1,
        mp_group=None,
        activation=torch.nn.GELU(),
        gate=NaiveGate,
        top_k=2,
        expert_dp_comm="none",
        gate_hook=None,
        mask=None,
        mask_dict=None,
        expert=_LinearExpert,
        kernel_size=1,
        dilation=1
    ):
        super().__init__(
            num_expert=num_expert,
            d_model=d_model,
            gate=gate,
            top_k=top_k,
            world_size=world_size,
            mp_group=mp_group,
            gate_hook=gate_hook,
            mask=mask,
            mask_dict=mask_dict
        )

        if expert == _LinearExpert:
            self.experts = expert(
                num_expert, d_model, d_hidden, activation, rank=self.mp_rank)
        elif expert == _ConvExpert:
                self.experts = expert(
                num_expert, d_model, d_hidden, kernel_size, dilation, activation, rank=self.mp_rank)
        self.mark_parallel_comm(expert_dp_comm)

    def forward(self, inp: torch.Tensor, gating_features=None):
        r"""
        This module wraps up the FMoE module with reshape, residual and layer
        normalization.
        """
        original_shape = inp.shape
        inp = inp.reshape(-1, self.d_model)
        if gating_features != None:
            gating_features = gating_features.reshape(-1, self.d_model)
        output = super().forward(inp, gating_features)
        return output.reshape(original_shape)

    

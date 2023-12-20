from trainer import ToyTrmTrainer
from logistic_trainer import LogisticTrainer
from torch.func import functional_call, grad, vmap, hessian, grad_and_value, jvp, vjp
import torch
import torch.nn as nn
import cvxpy as cp
import os
import time
from tqdm import tqdm


def proj_alpha(optimizer, args, kwargs):
    for p in tqdm(optimizer.param_groups[0]["params"], desc="Solving Projection"):
        data = p.data
        data_cpu = data.squeeze().cpu().numpy()
        data_proj = cp.Variable(data.size(0))
        objective = cp.Minimize(cp.sum_squares(data_cpu - data_proj))
        prob = cp.Problem(objective, [cp.sum(data_proj) == 1, data_proj >= 0])
        result = prob.solve()
        data_res = torch.tensor(data_proj.value).view(data.size()).to(data.device).to(data.dtype)
        p.data = data_res


class GradLayerFunction(torch.autograd.Function):    
    @staticmethod
    def forward(ctx, theta, alpha, model, xn, yn, eta, t):
        params = model.vector_to_params(theta)
        buffers = {n: b.detach() for n, b in model.named_buffers()}
        g, l = grad_and_value(model.compute_loss_func)(params, buffers, model, xn, yn, alpha=alpha)
        ctx.save_for_backward(theta, alpha, xn, yn)
        ctx.model = model
        ctx.eta = eta
        ctx.t = t
        
        new_theta = theta.clone()
        new_theta.add_(model.params_to_vector(g), alpha=-eta)
        return new_theta

    @staticmethod
    def backward(ctx, grad_output):
        if ctx.t % 1000 == 0:
            print("Backward", ctx.t)

        theta, alpha, xn, yn = ctx.saved_tensors
        model = ctx.model
        eta = ctx.eta
        params = model.vector_to_params(theta)
        buffers = {n: b.detach() for n, b in model.named_buffers()}
        vmapped_grad_func = vmap(grad(model.compute_loss_func_single), in_dims=(None, None, None, 0, 0))
        vmapped_g = vmapped_grad_func(params, buffers, model, xn, yn)
                
        grad_output_params = model.vector_to_params(grad_output)
        IF_abs = torch.zeros_like(alpha)
        for n, _ in model.named_parameters():
            x1 = grad_output_params[n].view(-1)
            x2 = vmapped_g[n].contiguous().view(vmapped_g[n].size(0), -1)
            IF_abs += x2 @ x1
        
        grad_alpha = -IF_abs * eta

        def hvp_fwdrev(f, primals, tangents):
            def grad_wrapper(pr):
                g = grad(f)(pr, buffers, model, xn, yn, alpha=alpha)
                return g
            return jvp(grad_wrapper, primals, tangents)[1]
        
        def hvp_revrev(f, primals, tangents):
            def grad_wrapper(pr):
                g = grad(f)(pr, buffers, model, xn, yn, alpha=alpha)
                return g
            vjpfunc = vjp(grad_wrapper, primals[0])[1]
            return vjpfunc(tangents[0])[0]
        
        hvp = hvp_fwdrev(model.compute_loss_func, (params,), (grad_output_params,))
        
        hvp_vec = model.params_to_vector(hvp)
        
        theta_grad = grad_output - eta * hvp_vec
        
        return theta_grad, grad_alpha, None, None, None, None, None


class DevGradLayerFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, theta, model, dev_xn, dev_yn):
        params = model.vector_to_params(theta)
        buffers = {n: b.detach() for n, b in model.named_buffers()}
        dev_loss = model.compute_loss_func(params, buffers, model, dev_xn, dev_yn)
        ctx.save_for_backward(dev_xn, dev_yn)
        ctx.model = model
        ctx.params = params
        ctx.buffers = buffers
        
        return dev_loss
    
    @staticmethod
    def backward(ctx, grad_output):
        dev_xn, dev_yn = ctx.saved_tensors
        g_dev = grad(ctx.model.compute_loss_func)(ctx.params, ctx.buffers, ctx.model, dev_xn, dev_yn)
        g_dev = ctx.model.params_to_vector(g_dev) * grad_output
        return g_dev, None, None, None, None


class AlphaModel(nn.Module):
    def __init__(self, n_alpha, n_steps) -> None:
        super().__init__()
        self.n_alpha = n_alpha
        self.n_steps = n_steps
        self.alpha = nn.ParameterList(
            [nn.Parameter(torch.ones(n_alpha) / n_alpha) for _ in range(n_steps)])
        
    def forward(self, theta, model, xn, yn, dev_xn, dev_yn, eta):
        all_losses, all_logging_losses = [], []
        area_loss = 0
        st = time.time()
        for t in tqdm(range(self.n_steps), desc="Forward"):
            theta = GradLayerFunction.apply(theta, self.alpha[t], model, xn, yn, eta, t)
            loss = DevGradLayerFunction.apply(theta, model, dev_xn, dev_yn)
            if t % 100 == 0:
                print("Forward | t: {} | inner loss: {:.4f}".format(t, loss.item()))
                all_logging_losses.append(round(loss.item(), 4))
            
            all_losses.append(loss.item())
            area_loss += loss
        area_loss = area_loss / self.n_steps
        return area_loss, all_losses, all_logging_losses

    
class OptAlphaTrainer():
    def __init__(self, args, device) -> None:
        
        # self.base_trainer = ToyTrmTrainer(args, device)
        self.base_trainer = LogisticTrainer(args, device)
        
        self.model = self.base_trainer.model
        self.train_data = self.base_trainer.train_data
        self.dev_data = self.base_trainer.dev_data
        self.test_data = self.base_trainer.test_data
        self.args = args
        self.device = device
        
        self.outer_epochs = 40
        self.outer_lr = 0.005
        self.alpha_model = AlphaModel(self.train_data[0].size(0), args.epochs).to(device)
        self.optimizer = torch.optim.SGD(self.alpha_model.parameters(), lr=self.outer_lr)
        self.optimizer.register_step_post_hook(proj_alpha)
    
    def train(self):
        params = {n: p.detach() for n, p in self.model.named_parameters()}
        theta = self.model.params_to_vector(params)
        xn, yn = self.train_data
        dev_xn, dev_yn = self.dev_data
        for e in range(self.outer_epochs):
            st = time.time()
            self.optimizer.zero_grad()
            area_loss, all_losses, all_logging_losses = self.alpha_model(
                theta, self.model, xn, yn, dev_xn, dev_yn, self.args.lr)
            forward_elapsed = time.time() - st
            area_loss.backward()
            backward_elapsed = time.time() - st - forward_elapsed
            
            print("epoch {} | train area loss {:.4f}".format(e, area_loss.item()))
            print("All Losses", all_logging_losses)
            
            self.optimizer.step()
            step_elapsed = time.time() - st - forward_elapsed - backward_elapsed
            
            print("Forward Elapsed: {:.4f} | Backward Elapsed: {:.4f} | Step Elapsed: {:.4f}".format(
                forward_elapsed, backward_elapsed, step_elapsed))
            

                
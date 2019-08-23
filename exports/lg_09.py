
#################################################
### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
#################################################
# file to edit: deeplearning-ff/09_optimizers.ipynb

from exports.lg_08 import *

def sgd_step(p, lr, **kwargs):
    """Stepper to pass to Optimizer(). kwargs are hypers"""
    p.data.add_(-lr, p.grad.data) # this adds the product of whats inside parenthesis
    return p

class Recorder(Callback):
    def begin_fit(self): self.lrs,self.losses = [],[]

    def after_batch(self):
        if not self.in_train: return
        self.lrs.append(self.opt.hypers[-1]['lr'])
        self.losses.append(self.loss.detach().cpu())

    def plot_lr  (self): plt.plot(self.lrs)
    def plot_loss(self): plt.plot(self.losses)

    def plot(self, skip_last=0):
        losses = [o.item() for o in self.losses]
        n = len(losses)-skip_last
        plt.xscale('log')
        plt.plot(self.lrs[:n], losses[:n])

class ParamScheduler(Callback):
    _order=1
    def __init__(self, pname, sched_funcs):
        self.pname,self.sched_funcs = pname,listify(sched_funcs)

    def begin_batch(self):
        if not self.in_train: return
        fs = self.sched_funcs
        if len(fs)==1: fs = fs*len(self.opt.param_groups)
        pos = self.n_epochs/self.epochs
        #no need to calculate whole schedule ahead of time: at each point calc hypers for that time
        for f,h in zip(fs,self.opt.hypers): h[self.pname] = f(pos)

class LR_Find(Callback):
    _order=1
    def __init__(self, max_iter=100, min_lr=1e-6, max_lr=10):
        self.max_iter,self.min_lr,self.max_lr = max_iter,min_lr,max_lr
        self.best_loss = 1e9

    def begin_batch(self):
        if not self.in_train: return
        pos = self.n_iter/self.max_iter
        lr = self.min_lr * (self.max_lr/self.min_lr) ** pos
        for pg in self.opt.hypers: pg['lr'] = lr

    def after_step(self):
        if self.n_iter>=self.max_iter or self.loss>self.best_loss*10:
            raise CancelTrainException()
        if self.loss < self.best_loss: self.best_loss = self.loss

def weight_decay(p, lr, wd, **kwargs):
    p.data.mul_(1 - lr*wd) #wd_step appplied before sgd_step; here dont affect grads
    return p
weight_decay._defaults = dict(wd=0.)
#We need this function to have an attribute _defaults so that we are sure there
#is an hyper-parameter of the same name in our Optimizer.

def l2_reg(p, lr, wd, **kwargs):
    p.grad.data.add_(wd, p.data) #wd_step appplied before sgd_step; here grads changed
    return p
l2_reg._defaults = dict(wd=0.)
#We need this function to have an attribute _defaults so that we are sure there
#is an hyper-parameter of the same name in our Optimizer.

def maybe_update(os, dest, f):
    """To update defaults in Optimizer, if needed"""
    for o in os:
        for k,v in f(o).items():
            if k not in dest: dest[k] = v

def get_defaults(d): return getattr(d,'_defaults',{}) # get _defaults attr for each stepper func

class Optimizer():
    def __init__(self, params, steppers, **defaults):
        self.steppers = listify(steppers)#steppers as list
        maybe_update(self.steppers, defaults, get_defaults)
        #helper function adds in dest the key/values it finds while going
        #through steppers and applying get_defaults when they was no key of the same name.
        #At this point defaults contains hyperparam defaults from all steppers
        # might be a generator
        self.param_groups = list(params) # turn generator to list of params
        # ensure params is a list of lists; each list is list of params for one part of arch
        if not isinstance(self.param_groups[0], list): self.param_groups = [self.param_groups]
        self.hypers = [{**defaults} for p in self.param_groups] #list of dicts
        #each param group can have own set of hyperparams in dicts

    def grad_params(self):
        return [(p,hyper) for pg,hyper in zip(self.param_groups,self.hypers)
            for p in pg if p.grad is not None]

    def zero_grad(self):
        for p,hyper in self.grad_params():
            p.grad.detach_() # torch.Tensor.detach(), remove gradient computation history
            p.grad.zero_()

    def step(self):
        """This step func, in the base class, does nothing but
        delegate to stepper it was passed in. Successively"""
        for p,hyper in self.grad_params(): compose(p, self.steppers, **hyper)

sgd_opt = partial(Optimizer, steppers=[weight_decay, sgd_step]) #we fix the steppers for ease
# only defaults left to pass

class StatefulOptimizer(Optimizer):
    def __init__(self, params, steppers, stats=None, **defaults):
        self.stats = listify(stats)
        # add stat class, what func to run to populate state dict
        maybe_update(self.stats, defaults, get_defaults)
        super().__init__(params, steppers, **defaults)
        self.state = {} # add state ie, what happened last time; dict of dicts

    def step(self):
        for p,hyper in self.grad_params():
            if p not in self.state:
                #Create a state for p and call all the statistics to initialize it.
                self.state[p] = {}
                maybe_update(self.stats, self.state[p], lambda o: o.init_state(p))
            state = self.state[p]
            for stat in self.stats: state = stat.update(p, state, **hyper)
            compose(p, self.steppers, **state, **hyper)
            self.state[p] = state

class Stat():
    _defaults = {}
    def init_state(self, p): raise NotImplementedError
    def update(self, p, state, **kwargs): raise NotImplementedError

def momentum_step(p, lr, grad_avg, **kwargs):
    """Apply sgd step with grad_avg instead of grad"""
    p.data.add_(-lr, grad_avg)
    return p

def lin_comb(v1, v2, beta):
  """exponentially weighted moving avg; lerp in pytorch (mom reversed)"""
  return beta*v1 + (1-beta)*v2

class AverageGrad(Stat):
    _defaults = dict(mom=0.9) # mom is beta1

    def __init__(self, dampening:bool=False): self.dampening=dampening #no damp by default
    def init_state(self, p): return {'grad_avg': torch.zeros_like(p.grad.data)}
    def update(self, p, state, mom, **kwargs):
        state['mom_damp'] = 1-mom if self.dampening else 1.
        state['grad_avg'].mul_(mom).add_(state['mom_damp'], p.grad.data) # beta1 * before + (1-beta1)*now is added
        return state

class AverageSqrGrad(Stat):
    _defaults = dict(sqr_mom=0.99) # sqr_mom is beta2

    def __init__(self, dampening:bool=True): self.dampening=dampening # default dampening to True
    def init_state(self, p): return {'sqr_avg': torch.zeros_like(p.grad.data)}
    def update(self, p, state, sqr_mom, **kwargs):
        state['sqr_damp'] = 1-sqr_mom if self.dampening else 1.
        state['sqr_avg'].mul_(sqr_mom).addcmul_(state['sqr_damp'], p.grad.data, p.grad.data) # adds (cumsum of arg2 *arg3) ie dot-product
        return state

class StepCount(Stat):
    """Used for debiasing. t in beta**(t+1)"""
    def init_state(self, p): return {'step': 0}
    def update(self, p, state, **kwargs):
        state['step'] += 1
        return state

def debias(mom, damp, step): return damp * (1 - mom**step) / (1-mom)

def adam_step(p, lr, mom, mom_damp, step, sqr_mom, sqr_damp, grad_avg, sqr_avg, eps, **kwargs):
    """Adam is dampened-debiased momentum divided by dampended debiased Root Mean Square of grads.
    Division is adaptiveness, RMS-prop. """
    debias1 = debias(mom,     mom_damp, step)
    debias2 = debias(sqr_mom, sqr_damp, step)
    p.data.addcdiv_(-lr / debias1, grad_avg, (sqr_avg/debias2).sqrt() + eps) # adds cumsum of grad_avg / ((x).sqrt()+eps)
    #use eps 0.001~0.1 to avoid blowing up denom (too small eps is risky)
    return p
adam_step._defaults = dict(eps=1e-5)

def adam_opt(xtra_step=None, **kwargs):
    return partial(StatefulOptimizer, steppers=[adam_step,weight_decay]+listify(xtra_step),
                   stats=[AverageGrad(dampening=True), AverageSqrGrad(), StepCount()], **kwargs)

def lamb_step(p, lr, mom, mom_damp, step, sqr_mom, sqr_damp, grad_avg, sqr_avg, eps, wd, **kwargs):
    """First five lines are Adam.
    r1 is norm of weights ( so sqrt of what u mult lamda/wd by in L2 reg), so built-in time-decay s is Adam step
    r2 is norm of Adam step
    Lamb is Adam step, with wieght deacy,  but w layer-wise scaling of lr
    (instead of dividing (adapting chg speed) individual params/grad with their own RMS,
    we divide by that averaged over the layer) ---> less noisy, bumpy"""
    debias1 = debias(mom,     mom_damp, step)
    debias2 = debias(sqr_mom, sqr_damp, step)
    r1 = p.data.pow(2).mean().sqrt() #averaging over layer? in paper, this convert step to same scale as weight. Phi() bits
    step = (grad_avg/debias1) / ((sqr_avg/debias2).sqrt()+eps) + wd*p.data #gradient bits. adaptiveness here, weight decay as well
    r2 = step.pow(2).mean().sqrt() # layerwised here, RMS of gradients (unbiasedly ADAPTIVE gradients )
    p.data.add_(-lr * min(r1/r2,10), step) #layer-wise scaling of lr, step is adaptive element-wise
    return p
lamb_step._defaults = dict(eps=1e-6, wd=0.)

lamb_opt = partial(StatefulOptimizer, steppers=lamb_step, stats=[AverageGrad(dampening=True), AverageSqrGrad(), StepCount()])
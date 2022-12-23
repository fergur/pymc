#   Copyright 2023 The PyMC Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
#   MIT License
#
#   Copyright (c) 2021-2022 aesara-devs
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.

import warnings

import numpy as np
import pytensor
import pytensor.tensor as at
import pytest
import scipy.stats.distributions as sp

from pytensor.graph.basic import ancestors, equal_computations
from pytensor.tensor.random.op import RandomVariable
from pytensor.tensor.subtensor import AdvancedSubtensor, AdvancedSubtensor1, Subtensor

from pymc.logprob.abstract import logprob
from pymc.logprob.joint_logprob import factorized_joint_logprob
from pymc.logprob.utils import rvs_to_value_vars, walk_model
from pymc.tests.helpers import assert_no_rvs
from pymc.tests.logprob.utils import joint_logprob


def test_joint_logprob_basic():
    # A simple check for when `joint_logprob` is the same as `logprob`
    a = at.random.uniform(0.0, 1.0)
    a.name = "a"
    a_value_var = a.clone()

    a_logp = joint_logprob({a: a_value_var}, sum=False)
    a_logp_exp = logprob(a, a_value_var)

    assert equal_computations([a_logp], [a_logp_exp])

    # Let's try a hierarchical model
    sigma = at.random.invgamma(0.5, 0.5)
    Y = at.random.normal(0.0, sigma)

    sigma_value_var = sigma.clone()
    y_value_var = Y.clone()

    total_ll = joint_logprob({Y: y_value_var, sigma: sigma_value_var}, sum=False)

    # We need to replace the reference to `sigma` in `Y` with its value
    # variable
    ll_Y = logprob(Y, y_value_var)
    (ll_Y,), _ = rvs_to_value_vars(
        [ll_Y],
        initial_replacements={sigma: sigma_value_var},
    )
    total_ll_exp = logprob(sigma, sigma_value_var) + ll_Y

    assert equal_computations([total_ll], [total_ll_exp])

    # Now, make sure we can compute a joint log-probability for a hierarchical
    # model with some non-`RandomVariable` nodes
    c = at.random.normal()
    c.name = "c"
    b_l = c * a + 2.0
    b = at.random.uniform(b_l, b_l + 1.0)
    b.name = "b"

    b_value_var = b.clone()
    c_value_var = c.clone()

    b_logp = joint_logprob({a: a_value_var, b: b_value_var, c: c_value_var})

    # There shouldn't be any `RandomVariable`s in the resulting graph
    assert_no_rvs(b_logp)

    res_ancestors = list(walk_model((b_logp,), walk_past_rvs=True))
    assert b_value_var in res_ancestors
    assert c_value_var in res_ancestors
    assert a_value_var in res_ancestors


def test_joint_logprob_multi_obs():
    a = at.random.uniform(0.0, 1.0)
    b = at.random.normal(0.0, 1.0)

    a_val = a.clone()
    b_val = b.clone()

    logp = joint_logprob({a: a_val, b: b_val}, sum=False)
    logp_exp = logprob(a, a_val) + logprob(b, b_val)

    assert equal_computations([logp], [logp_exp])

    x = at.random.normal(0, 1)
    y = at.random.normal(x, 1)

    x_val = x.clone()
    y_val = y.clone()

    logp = joint_logprob({x: x_val, y: y_val})
    exp_logp = joint_logprob({x: x_val, y: y_val})

    assert equal_computations([logp], [exp_logp])


def test_joint_logprob_diff_dims():
    M = at.matrix("M")
    x = at.random.normal(0, 1, size=M.shape[1], name="X")
    y = at.random.normal(M.dot(x), 1, name="Y")

    x_vv = x.clone()
    x_vv.name = "x"
    y_vv = y.clone()
    y_vv.name = "y"

    logp = joint_logprob({x: x_vv, y: y_vv})

    M_val = np.random.normal(size=(10, 3))
    x_val = np.random.normal(size=(3,))
    y_val = np.random.normal(size=(10,))

    point = {M: M_val, x_vv: x_val, y_vv: y_val}
    logp_val = logp.eval(point)

    exp_logp_val = (
        sp.norm.logpdf(x_val, 0, 1).sum() + sp.norm.logpdf(y_val, M_val.dot(x_val), 1).sum()
    )
    assert exp_logp_val == pytest.approx(logp_val)


def test_incsubtensor_original_values_output_dict():
    """
    Test that the original un-incsubtensor value variable appears an the key of
    the logprob factor
    """

    base_rv = at.random.normal(0, 1, size=2)
    rv = at.set_subtensor(base_rv[0], 5)
    vv = rv.clone()

    logp_dict = factorized_joint_logprob({rv: vv})
    assert vv in logp_dict


def test_joint_logprob_subtensor():
    """Make sure we can compute a joint log-probability for ``Y[I]`` where ``Y`` and ``I`` are random variables."""

    size = 5

    mu_base = np.power(10, np.arange(np.prod(size))).reshape(size)
    mu = np.stack([mu_base, -mu_base])
    sigma = 0.001
    rng = pytensor.shared(np.random.RandomState(232), borrow=True)

    A_rv = at.random.normal(mu, sigma, rng=rng)
    A_rv.name = "A"

    p = 0.5

    I_rv = at.random.bernoulli(p, size=size, rng=rng)
    I_rv.name = "I"

    A_idx = A_rv[I_rv, at.ogrid[A_rv.shape[-1] :]]

    assert isinstance(A_idx.owner.op, (Subtensor, AdvancedSubtensor, AdvancedSubtensor1))

    A_idx_value_var = A_idx.type()
    A_idx_value_var.name = "A_idx_value"

    I_value_var = I_rv.type()
    I_value_var.name = "I_value"

    A_idx_logp = joint_logprob({A_idx: A_idx_value_var, I_rv: I_value_var}, sum=False)

    logp_vals_fn = pytensor.function([A_idx_value_var, I_value_var], A_idx_logp)

    # The compiled graph should not contain any `RandomVariables`
    assert_no_rvs(logp_vals_fn.maker.fgraph.outputs[0])

    decimals = 6 if pytensor.config.floatX == "float64" else 4

    test_val_rng = np.random.RandomState(3238)

    for i in range(10):
        bern_sp = sp.bernoulli(p)
        I_value = bern_sp.rvs(size=size, random_state=test_val_rng).astype(I_rv.dtype)

        norm_sp = sp.norm(mu[I_value, np.ogrid[mu.shape[1] :]], sigma)
        A_idx_value = norm_sp.rvs(random_state=test_val_rng).astype(A_idx.dtype)

        exp_obs_logps = norm_sp.logpdf(A_idx_value)
        exp_obs_logps += bern_sp.logpmf(I_value)

        logp_vals = logp_vals_fn(A_idx_value, I_value)

        np.testing.assert_almost_equal(logp_vals, exp_obs_logps, decimal=decimals)


def test_persist_inputs():
    """Make sure we don't unnecessarily clone variables."""
    x = at.scalar("x")
    beta_rv = at.random.normal(0, 1, name="beta")
    Y_rv = at.random.normal(beta_rv * x, 1, name="y")

    beta_vv = beta_rv.type()
    y_vv = Y_rv.clone()

    logp = joint_logprob({beta_rv: beta_vv, Y_rv: y_vv})

    assert x in ancestors([logp])

    # Make sure we don't clone value variables when they're graphs.
    y_vv_2 = y_vv * 2
    logp_2 = joint_logprob({beta_rv: beta_vv, Y_rv: y_vv_2})

    assert y_vv_2 in ancestors([logp_2])


def test_warn_random_not_found():
    x_rv = at.random.normal(name="x")
    y_rv = at.random.normal(x_rv, 1, name="y")

    y_vv = y_rv.clone()

    with pytest.warns(UserWarning):
        factorized_joint_logprob({y_rv: y_vv})

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        factorized_joint_logprob({y_rv: y_vv}, warn_missing_rvs=False)


def test_multiple_rvs_to_same_value_raises():
    x_rv1 = at.random.normal(name="x1")
    x_rv2 = at.random.normal(name="x2")
    x = x_rv1.type()
    x.name = "x"

    msg = "More than one logprob factor was assigned to the value var x"
    with pytest.raises(ValueError, match=msg):
        joint_logprob({x_rv1: x, x_rv2: x})


def test_get_scaling():

    assert _get_scaling(None, (2, 3), 2).eval() == 1
    # ndim >=1 & ndim<1
    assert _get_scaling(45, (2, 3), 1).eval() == 22.5
    assert _get_scaling(45, (2, 3), 0).eval() == 45

    # list or tuple tests
    # total_size contains other than Ellipsis, None and Int
    with pytest.raises(TypeError, match="Unrecognized `total_size` type"):
        _get_scaling([2, 4, 5, 9, 11.5], (2, 3), 2)
    # check with Ellipsis
    with pytest.raises(ValueError, match="Double Ellipsis in `total_size` is restricted"):
        _get_scaling([1, 2, 5, Ellipsis, Ellipsis], (2, 3), 2)
    with pytest.raises(
        ValueError,
        match="Length of `total_size` is too big, number of scalings is bigger that ndim",
    ):
        _get_scaling([1, 2, 5, Ellipsis], (2, 3), 2)

    assert _get_scaling([Ellipsis], (2, 3), 2).eval() == 1

    assert _get_scaling([4, 5, 9, Ellipsis, 32, 12], (2, 3, 2), 5).eval() == 960
    assert _get_scaling([4, 5, 9, Ellipsis], (2, 3, 2), 5).eval() == 15
    # total_size with no Ellipsis (end = [ ])
    with pytest.raises(
        ValueError,
        match="Length of `total_size` is too big, number of scalings is bigger that ndim",
    ):
        _get_scaling([1, 2, 5], (2, 3), 2)

    assert _get_scaling([], (2, 3), 2).eval() == 1
    assert _get_scaling((), (2, 3), 2).eval() == 1
    # total_size invalid type
    with pytest.raises(
        TypeError,
        match="Unrecognized `total_size` type, expected int or list of ints, got {1, 2, 5}",
    ):
        _get_scaling({1, 2, 5}, (2, 3), 2)

    # test with rvar from model graph
    with pm.Model() as m2:
        rv_var = pm.Uniform("a", 0.0, 1.0)
    total_size = []
    assert _get_scaling(total_size, shape=rv_var.shape, ndim=rv_var.ndim).eval() == 1.0


def test_joint_logp_basic():
    """Make sure we can compute a log-likelihood for a hierarchical model with transforms."""

    with pm.Model() as m:
        a = pm.Uniform("a", 0.0, 1.0)
        c = pm.Normal("c")
        b_l = c * a + 2.0
        b = pm.Uniform("b", b_l, b_l + 1.0)

    a_value_var = m.rvs_to_values[a]
    assert m.rvs_to_transforms[a]

    b_value_var = m.rvs_to_values[b]
    assert m.rvs_to_transforms[b]

    c_value_var = m.rvs_to_values[c]

    (b_logp,) = joint_logp(
        (b,),
        rvs_to_values=m.rvs_to_values,
        rvs_to_transforms=m.rvs_to_transforms,
        rvs_to_total_sizes={},
    )

    # There shouldn't be any `RandomVariable`s in the resulting graph
    assert_no_rvs(b_logp)

    res_ancestors = list(walk_model((b_logp,)))
    assert b_value_var in res_ancestors
    assert c_value_var in res_ancestors
    assert a_value_var in res_ancestors


def test_joint_logp_subtensor():
    """Make sure we can compute a log-likelihood for ``Y[I]`` where ``Y`` and ``I`` are random variables."""

    size = 5

    mu_base = pm.floatX(np.power(10, np.arange(np.prod(size)))).reshape(size)
    mu = np.stack([mu_base, -mu_base])
    sigma = 0.001
    rng = pytensor.shared(np.random.RandomState(232), borrow=True)

    A_rv = pm.Normal.dist(mu, sigma, rng=rng)
    A_rv.name = "A"

    p = 0.5

    I_rv = pm.Bernoulli.dist(p, size=size, rng=rng)
    I_rv.name = "I"

    A_idx = A_rv[I_rv, at.ogrid[A_rv.shape[-1] :]]

    assert isinstance(A_idx.owner.op, (Subtensor, AdvancedSubtensor, AdvancedSubtensor1))

    A_idx_value_var = A_idx.type()
    A_idx_value_var.name = "A_idx_value"

    I_value_var = I_rv.type()
    I_value_var.name = "I_value"

    A_idx_logps = joint_logp(
        (A_idx, I_rv),
        rvs_to_values={A_idx: A_idx_value_var, I_rv: I_value_var},
        rvs_to_transforms={},
        rvs_to_total_sizes={},
    )
    A_idx_logp = at.add(*A_idx_logps)

    logp_vals_fn = pytensor.function([A_idx_value_var, I_value_var], A_idx_logp)

    # The compiled graph should not contain any `RandomVariables`
    assert_no_rvs(logp_vals_fn.maker.fgraph.outputs[0])

    decimals = select_by_precision(float64=6, float32=4)

    for i in range(10):
        bern_sp = sp.bernoulli(p)
        I_value = bern_sp.rvs(size=size).astype(I_rv.dtype)

        norm_sp = sp.norm(mu[I_value, np.ogrid[mu.shape[1] :]], sigma)
        A_idx_value = norm_sp.rvs().astype(A_idx.dtype)

        exp_obs_logps = norm_sp.logpdf(A_idx_value)
        exp_obs_logps += bern_sp.logpmf(I_value)

        logp_vals = logp_vals_fn(A_idx_value, I_value)

        np.testing.assert_almost_equal(logp_vals, exp_obs_logps, decimal=decimals)


def test_logp_helper():
    value = at.vector("value")
    x = pm.Normal.dist(0, 1)

    x_logp = pm.logp(x, value)
    np.testing.assert_almost_equal(x_logp.eval({value: [0, 1]}), sp.norm(0, 1).logpdf([0, 1]))

    x_logp = pm.logp(x, [0, 1])
    np.testing.assert_almost_equal(x_logp.eval(), sp.norm(0, 1).logpdf([0, 1]))


def test_logp_helper_derived_rv():
    assert np.isclose(
        pm.logp(at.exp(pm.Normal.dist()), 5).eval(),
        pm.logp(pm.LogNormal.dist(), 5).eval(),
    )


def test_logp_helper_exceptions():
    with pytest.raises(TypeError, match="When RV is not a pure distribution"):
        pm.logp(at.exp(pm.Normal.dist()), [1, 2])

    with pytest.raises(NotImplementedError, match="PyMC could not infer logp of input variable"):
        pm.logp(at.cos(pm.Normal.dist()), 1)


def test_model_unchanged_logprob_access():
    # Issue #5007
    with pm.Model() as model:
        a = pm.Normal("a")
        c = pm.Uniform("c", lower=a - 1, upper=1)

    original_inputs = set(pytensor.graph.graph_inputs([c]))
    # Extract model.logp
    model.logp()
    new_inputs = set(pytensor.graph.graph_inputs([c]))
    assert original_inputs == new_inputs


def test_unexpected_rvs():
    with pm.Model() as model:
        x = pm.Normal("x")
        y = pm.CustomDist("y", logp=lambda *args: x)

    with pytest.raises(ValueError, match="^Random variables detected in the logp graph"):
        model.logp()


def test_hierarchical_logp():
    """Make sure there are no random variables in a model's log-likelihood graph."""
    with pm.Model() as m:
        x = pm.Uniform("x", lower=0, upper=1)
        y = pm.Uniform("y", lower=0, upper=x)

    logp_ancestors = list(ancestors([m.logp()]))
    ops = {a.owner.op for a in logp_ancestors if a.owner}
    assert len(ops) > 0
    assert not any(isinstance(o, RandomVariable) for o in ops)
    assert m.rvs_to_values[x] in logp_ancestors
    assert m.rvs_to_values[y] in logp_ancestors


def test_hierarchical_obs_logp():
    obs = np.array([0.5, 0.4, 5, 2])

    with pm.Model() as model:
        x = pm.Uniform("x", 0, 1, observed=obs)
        pm.Uniform("y", x, 2, observed=obs)

    logp_ancestors = list(ancestors([model.logp()]))
    ops = {a.owner.op for a in logp_ancestors if a.owner}
    assert len(ops) > 0
    assert not any(isinstance(o, RandomVariable) for o in ops)


def test_logprob_join_constant_shapes():
    x = at.random.normal(size=5)
    y = at.random.normal(size=3)
    xy = at.join(x, y)
    xy_vv = at.vector("xy_vv")

    xy_logp = pm.logp(xy, xy_vv)
    # This is what Aeppl does not do!
    assert_no_rvs(xy_logp)

    f = pytensor.function([xy_vv], xy_logp)
    np.testing.assert_array_equal(f(np.zeros(8)), sp.norm.logpdf(np.zeros(8)))

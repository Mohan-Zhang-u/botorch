#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from typing import List

import torch
from botorch.acquisition import LinearMCObjective, ScalarizedPosteriorTransform
from botorch.acquisition.acquisition import AcquisitionFunction
from botorch.acquisition.analytic import ExpectedImprovement
from botorch.acquisition.monte_carlo import qExpectedImprovement
from botorch.acquisition.proximal import ProximalAcquisitionFunction
from botorch.exceptions.errors import UnsupportedError
from botorch.models import ModelListGP, SingleTaskGP
from botorch.models.gpytorch import GPyTorchModel
from botorch.models.model import Model
from botorch.models.transforms.input import Normalize
from botorch.utils.testing import BotorchTestCase
from torch.distributions.multivariate_normal import MultivariateNormal


class DummyModel(GPyTorchModel):
    num_outputs = 1

    def __init__(self):
        super(GPyTorchModel, self).__init__()

    def subset_output(self, idcs: List[int]) -> Model:
        pass


class DummyAcquisitionFunction(AcquisitionFunction):
    def forward(self, X):
        pass


class TestProximalAcquisitionFunction(BotorchTestCase):
    def test_proximal(self):
        for dtype in (torch.float, torch.double):
            train_X = torch.rand(5, 3, device=self.device, dtype=dtype) * 2.0
            train_Y = train_X.norm(dim=-1, keepdim=True)

            # test single point evaluation with and without input transform
            normalize = Normalize(
                3, bounds=torch.tensor(((0.0, 0.0, 0.0), (2.0, 2.0, 2.0)))
            )
            for input_transform in [None, normalize]:

                # test with and without transformed weights
                for transformed_weighting in [True, False]:
                    # test with single outcome model
                    model = (
                        SingleTaskGP(train_X, train_Y, input_transform=input_transform)
                        .to(device=self.device, dtype=dtype)
                        .eval()
                    )

                    EI = ExpectedImprovement(model, best_f=0.0)

                    proximal_weights = torch.ones(3, device=self.device, dtype=dtype)
                    last_X = train_X[-1]
                    test_X = torch.rand(1, 3, device=self.device, dtype=dtype)
                    EI_prox = ProximalAcquisitionFunction(
                        EI,
                        proximal_weights=proximal_weights,
                        transformed_weighting=transformed_weighting,
                    )

                    ei = EI(test_X)

                    # modify last_X/test_X depending on transformed_weighting
                    proximal_test_X = test_X.clone()
                    if transformed_weighting:
                        if input_transform is not None:
                            last_X = input_transform(train_X[-1])
                            proximal_test_X = input_transform(test_X)

                    mv_normal = MultivariateNormal(last_X, torch.diag(proximal_weights))
                    test_prox_weight = torch.exp(
                        mv_normal.log_prob(proximal_test_X)
                    ) / torch.exp(mv_normal.log_prob(last_X))

                    ei_prox = EI_prox(test_X)
                    self.assertTrue(torch.allclose(ei_prox, ei * test_prox_weight))
                    self.assertTrue(ei_prox.shape == torch.Size([1]))

                    # test t-batch with broadcasting
                    test_X = torch.rand(4, 1, 3, device=self.device, dtype=dtype)
                    proximal_test_X = test_X.clone()
                    if transformed_weighting:
                        if input_transform is not None:
                            last_X = input_transform(train_X[-1])
                            proximal_test_X = input_transform(test_X)

                    ei = EI(test_X)
                    mv_normal = MultivariateNormal(last_X, torch.diag(proximal_weights))
                    test_prox_weight = torch.exp(
                        mv_normal.log_prob(proximal_test_X)
                    ) / torch.exp(mv_normal.log_prob(last_X))

                    ei_prox = EI_prox(test_X)
                    self.assertTrue(
                        torch.allclose(ei_prox, ei * test_prox_weight.flatten())
                    )
                    self.assertTrue(ei_prox.shape == torch.Size([4]))

                    # test q-based MC acquisition function
                    qEI = qExpectedImprovement(model, best_f=0.0)
                    test_X = torch.rand(4, 1, 3, device=self.device, dtype=dtype)
                    proximal_test_X = test_X.clone()
                    if transformed_weighting:
                        if input_transform is not None:
                            last_X = input_transform(train_X[-1])
                            proximal_test_X = input_transform(test_X)

                    qEI_prox = ProximalAcquisitionFunction(
                        qEI,
                        proximal_weights=proximal_weights,
                        transformed_weighting=transformed_weighting,
                    )

                    qei = qEI(test_X)
                    mv_normal = MultivariateNormal(last_X, torch.diag(proximal_weights))
                    test_prox_weight = torch.exp(
                        mv_normal.log_prob(proximal_test_X)
                    ) / torch.exp(mv_normal.log_prob(last_X))

                    qei_prox = qEI_prox(test_X)
                    self.assertTrue(
                        torch.allclose(qei_prox, qei * test_prox_weight.flatten())
                    )
                    self.assertTrue(qei_prox.shape == torch.Size([4]))

            # test gradient
            test_X = torch.rand(
                1, 3, device=self.device, dtype=dtype, requires_grad=True
            )
            ei_prox = EI_prox(test_X)
            ei_prox.backward()

            # test model without train_inputs
            bad_model = DummyModel()
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(bad_model, 0.0), proximal_weights
                )

            # test proximal weights that do not match training_inputs
            train_X = torch.rand(5, 1, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)
            model = SingleTaskGP(train_X, train_Y).to(device=self.device).eval()
            with self.assertRaises(ValueError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(model, 0.0), proximal_weights[:1]
                )

            with self.assertRaises(ValueError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(model, 0.0),
                    torch.rand(3, 3, device=self.device, dtype=dtype),
                )

            # test for x_pending points
            pending_acq = DummyAcquisitionFunction(model)
            pending_acq.set_X_pending(torch.rand(3, 3, device=self.device, dtype=dtype))
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(pending_acq, proximal_weights)

            # test model with multi-batch training inputs
            train_X = torch.rand(5, 2, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)
            bad_single_task = (
                SingleTaskGP(train_X, train_Y).to(device=self.device).eval()
            )
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(bad_single_task, 0.0), proximal_weights
                )

            # test a multi-output SingleTaskGP model
            train_X = torch.rand(5, 3, device=self.device, dtype=dtype)
            train_Y = torch.rand(5, 2, device=self.device, dtype=dtype)

            multi_output_model = SingleTaskGP(train_X, train_Y).to(device=self.device)
            ptransform = ScalarizedPosteriorTransform(
                weights=torch.ones(2, dtype=dtype)
            )
            acq = ProximalAcquisitionFunction(
                ExpectedImprovement(
                    multi_output_model, 0.0, posterior_transform=ptransform
                ),
                proximal_weights,
            )
            acq(test_X)

    def test_proximal_model_list(self):
        for dtype in (torch.float, torch.double):
            proximal_weights = torch.ones(3, device=self.device, dtype=dtype)

            # test with model-list model for complex objective optimization
            train_X = torch.rand(5, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)

            gp = SingleTaskGP(train_X, train_Y).to(device=self.device)
            model = ModelListGP(gp, gp)

            scalarized_posterior_transform = ScalarizedPosteriorTransform(
                torch.ones(2, device=self.device, dtype=dtype)
            )
            mc_linear_objective = LinearMCObjective(
                torch.ones(2, device=self.device, dtype=dtype)
            )

            EI = ExpectedImprovement(
                model, best_f=0.0, posterior_transform=scalarized_posterior_transform
            )

            test_X = torch.rand(1, 3, device=self.device, dtype=dtype)
            EI_prox = ProximalAcquisitionFunction(EI, proximal_weights=proximal_weights)

            ei = EI(test_X)
            mv_normal = MultivariateNormal(train_X[-1], torch.diag(proximal_weights))
            test_prox_weight = torch.exp(mv_normal.log_prob(test_X)) / torch.exp(
                mv_normal.log_prob(train_X[-1])
            )

            # test calculation
            ei_prox = EI_prox(test_X)

            self.assertTrue(torch.allclose(ei_prox, ei * test_prox_weight))
            self.assertTrue(ei_prox.shape == torch.Size([1]))

            # test MC acquisition function
            qEI = qExpectedImprovement(model, best_f=0.0, objective=mc_linear_objective)
            test_X = torch.rand(4, 1, 3, device=self.device, dtype=dtype)
            qEI_prox = ProximalAcquisitionFunction(
                qEI, proximal_weights=proximal_weights
            )

            qei = qEI(test_X)
            mv_normal = MultivariateNormal(train_X[-1], torch.diag(proximal_weights))
            test_prox_weight = torch.exp(mv_normal.log_prob(test_X)) / torch.exp(
                mv_normal.log_prob(train_X[-1])
            )

            qei_prox = qEI_prox(test_X)
            self.assertTrue(torch.allclose(qei_prox, qei * test_prox_weight.flatten()))
            self.assertTrue(qei_prox.shape == torch.Size([4]))

            # test gradient
            test_X = torch.rand(
                1, 3, device=self.device, dtype=dtype, requires_grad=True
            )
            ei_prox = EI_prox(test_X)
            ei_prox.backward()

            # test proximal weights that do not match training_inputs
            expected_err_msg = (
                "`proximal_weights` must be a one dimensional tensor with "
                "same feature dimension as model."
            )
            with self.assertRaisesRegex(ValueError, expected_err_msg):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(
                        model, 0.0, posterior_transform=scalarized_posterior_transform
                    ),
                    proximal_weights[:1],
                )

            with self.assertRaisesRegex(ValueError, expected_err_msg):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(
                        model, 0.0, posterior_transform=scalarized_posterior_transform
                    ),
                    torch.rand(3, 3, device=self.device, dtype=dtype),
                )

            # test for x_pending points
            pending_acq = DummyAcquisitionFunction(model)
            pending_acq.set_X_pending(torch.rand(3, 3, device=self.device, dtype=dtype))
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(pending_acq, proximal_weights)

            # test model with multi-batch training inputs
            train_X = torch.rand(5, 2, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)
            bad_model = ModelListGP(
                SingleTaskGP(train_X, train_Y).to(device=self.device),
                SingleTaskGP(train_X, train_Y).to(device=self.device),
            )
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(
                        bad_model,
                        0.0,
                        posterior_transform=scalarized_posterior_transform,
                    ),
                    proximal_weights,
                )

            # try using unequal training sets
            train_X = torch.rand(5, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)
            bad_model = ModelListGP(
                SingleTaskGP(train_X[:-1], train_Y[:-1]).to(device=self.device),
                SingleTaskGP(train_X, train_Y).to(device=self.device),
            )
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(
                        bad_model,
                        0.0,
                        posterior_transform=scalarized_posterior_transform,
                    ),
                    proximal_weights,
                )

            # try with unequal input transforms
            train_X = torch.rand(5, 3, device=self.device, dtype=dtype)
            train_Y = train_X.norm(dim=-1, keepdim=True)
            bad_model = ModelListGP(
                SingleTaskGP(train_X, train_Y, input_transform=Normalize(3)).to(
                    device=self.device
                ),
                SingleTaskGP(train_X, train_Y).to(device=self.device),
            )
            with self.assertRaises(UnsupportedError):
                ProximalAcquisitionFunction(
                    ExpectedImprovement(
                        bad_model,
                        0.0,
                        posterior_transform=scalarized_posterior_transform,
                    ),
                    proximal_weights,
                )

"""refnl.py — the reference's integrator for nonlinear systems (AC machines): differential-algebraic equations
    B z' + A z + G F(t) + g(z) = 0          (B constant and possibly singular; g: the nonlinear terms)
written as M z' = f(t, z) with M = B, f = -(A z + G F(t) + g(z)).
Version: 2026.10.21.22

consistent(...)  initial values: Gauss-Newton (minimum norm) on the residual, the derivatives of the algebraic rows
                 and the given initial values, unknowns z0 and z0' (only z0 is used afterwards).
radau(...)       Radau IIA, 3 stages, order 5 (Hairer & Wanner, RADAU5): simplified Newton on the full 3n collocation
                 system, the embedded error estimate of RADAU5, step-size control; components constrained by the
                 algebraic equations (index 2) enter the error norm scaled by h. Dense output by the collocation
                 polynomial.
Independent of OpenModelica and of the Modelica text; numpy and scipy.linalg only.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.linalg import lu_factor, lu_solve

S6 = math.sqrt(6.0)
C = np.array([(4 - S6) / 10, (4 + S6) / 10, 1.0])
AR = np.array([[(88 - 7 * S6) / 360, (296 - 169 * S6) / 1800, (-2 + 3 * S6) / 225],
               [(296 + 169 * S6) / 1800, (88 + 7 * S6) / 360, (-2 - 3 * S6) / 225],
               [(16 - S6) / 36, (16 + S6) / 36, 1 / 9]])
# RADAU5's error estimate: gamma0 = 1/u1 (u1 the real eigenvalue of A^-1), dd = the embedded weights
_cbrt81, _cbrt9 = 81 ** (1 / 3), 9 ** (1 / 3)
U1 = 30 / (6 + _cbrt81 - _cbrt9)
DD = np.array([-(13 + 7 * S6) / 3, (-13 + 7 * S6) / 3, -1 / 3])
# A^-1 = T diag(gamma, [[alpha, -beta], [beta, alpha]]) T^-1: one real and one complex system per Newton step
AINV = np.linalg.inv(AR)
_ev, _V = np.linalg.eig(AINV)
_ir = int(np.argmin(np.abs(_ev.imag)))
_ic = [i for i in range(3) if i != _ir and _ev[i].imag > 0][0]
GAM = float(_ev[_ir].real)
ALC = complex(_ev[_ic])
TT = np.column_stack([_V[:, _ir].real / np.linalg.norm(_V[:, _ir].real), _V[:, _ic].real, -_V[:, _ic].imag])
TI = np.linalg.inv(TT)


class DAEError(RuntimeError):
    pass


class System:
    """f(t, z), its Jacobian J(t, z) = df/dz and the mass matrix M for the integrator."""

    def __init__(self, M, f, jac):
        self.M, self.f, self.jac = M, f, jac


def consistent(sysm, t0, z_guess, Lsel, vals, alg_rows, dF_dt, tol=1e-12, it=40):
    """z0 with residual 0, Lsel z0 = vals and the algebraic rows' time derivatives 0 (consistent z0'). dF_dt(t):
    the explicit time derivative of the residual rows (G F'(t)). Returns z0."""
    n = len(z_guess)
    M = sysm.M
    z, zd = np.array(z_guess, float), np.zeros(n)
    alg = np.array(alg_rows, int)
    for _ in range(it):
        J = sysm.jac(t0, z)                         # df/dz
        res_F = M @ zd - sysm.f(t0, z)             # B z' + A z + G F + g
        # algebraic rows: d/dt(residual) = (-J z') + dF_dt  (B rows are zero there)
        res_D = (-J @ zd + dF_dt(t0))[alg] if alg.size else np.zeros(0)
        res_L = Lsel @ z - vals if len(vals) else np.zeros(0)
        r = np.concatenate([res_F, res_D, res_L])
        if np.max(np.abs(r), initial=0) < tol * max(1.0, np.max(np.abs(z), initial=0)):
            return z
        # Jacobian in (z, zd); the algebraic rows' second-order term (d J/dz zd) is left out (Gauss-Newton)
        top = np.hstack([-J, M])
        mid = np.hstack([np.zeros((alg.size, n)), -J[alg]]) if alg.size else np.zeros((0, 2 * n))
        bot = np.hstack([Lsel, np.zeros((Lsel.shape[0], n))]) if len(vals) else np.zeros((0, 2 * n))
        K = np.vstack([top, mid, bot])
        cs = np.maximum(np.abs(K).max(axis=0), 1e-300)
        rs = np.maximum(np.abs(K).max(axis=1), 1e-300)
        d = np.linalg.lstsq((K / rs[:, None]) / cs[None, :], -r / rs, rcond=1e-13)[0] / cs
        z, zd = z + d[:n], zd + d[n:]
    r_end = np.concatenate([M @ zd - sysm.f(t0, z)])
    if np.max(np.abs(r_end), initial=0) > 1e-8 * max(1.0, np.max(np.abs(z), initial=0)):
        raise DAEError("no consistent initial values for the nonlinear system")
    return z


class Dense:
    """The collocation polynomial of one step: z(t) for t in [t0, t0 + h]."""

    def __init__(self, t0, h, z0, Z):
        self.t0, self.h, self.z0 = t0, h, z0
        # Lagrange interpolation through (0, z0), (c_i, z0 + Z_i)
        self.nodes = np.concatenate([[0.0], C])
        self.vals = np.vstack([z0, z0 + Z[0], z0 + Z[1], z0 + Z[2]])

    def __call__(self, t):
        s = (t - self.t0) / self.h
        out = np.zeros_like(self.z0)
        for i, xi in enumerate(self.nodes):
            li = 1.0
            for j, xj in enumerate(self.nodes):
                if j != i:
                    li *= (s - xj) / (xi - xj)
            out = out + li * self.vals[i]
        return out

    def deriv(self, t):
        s = (t - self.t0) / self.h
        out = np.zeros_like(self.z0)
        for i, xi in enumerate(self.nodes):
            tot = 0.0
            for k, xk in enumerate(self.nodes):
                if k == i:
                    continue
                prod = 1.0 / (xi - xk)
                for j, xj in enumerate(self.nodes):
                    if j not in (i, k):
                        prod *= (s - xj) / (xi - xj)
                tot += prod
            out = out + tot * self.vals[i]
        return out / self.h


def radau(sysm, t0, t1, z0, rtol=1e-9, atol=None, index2=None, h0=None, max_steps=400000, events=None, trace=None):
    """Integrate from t0 to t1. Returns (list of Dense steps, z(t1)). atol: per-component absolute tolerances;
    index2: boolean mask of components scaled by h in the error norm."""
    n = len(z0)
    M = sysm.M
    z = np.array(z0, float)
    atol = np.full(n, 1e-12) if atol is None else np.asarray(atol, float)
    # RADAU5's transformation: the estimate is of order 3 for a method of order 5
    quot = atol / rtol
    rtol = 0.1 * rtol ** (2 / 3)
    atol = rtol * quot
    i2 = np.zeros(n, bool) if index2 is None else np.asarray(index2, bool)
    span = t1 - t0
    zmax = np.abs(z).copy()                       # running maxima: oscillating variables pass through 0
    h = h0 or span * 1e-4
    h = min(h, span)
    t = t0
    steps = []
    I3 = np.eye(3)
    J = sysm.jac(t, z)
    fresh_jac = True
    lu = None
    first, reject = True, False
    fnewt = max(10 * 2.2e-16 / rtol, min(0.03, rtol ** 0.5))
    err_old, h_old = None, None
    nst = 0
    while t < t1 - 1e-14 * max(1.0, abs(t1)):
        nst += 1
        if nst > max_steps:
            raise DAEError(f"more than {max_steps} steps")
        if t + 1.5 * h >= t1:                       # no sliver of a step at the end
            if h != t1 - t:
                lu = None
            h = t1 - t
        if lu is None:
            lu = (lu_factor(GAM / h * M - J), lu_factor(ALC / h * M - J.astype(complex)))
        lur, luc = lu
        # simplified Newton on the stage increments Z (transformed: one real, one complex n x n system)
        Z = np.zeros((3, n))
        if steps and not reject:
            prev = steps[-1]
            for i in range(3):
                Z[i] = prev(t + C[i] * h) - z
        converged = False
        dn_old = None
        theta = 0.0
        ae = atol + 1e-2 * rtol * zmax
        scal = ae + rtol * np.abs(z)
        scal = np.where(i2, scal / max(h, 1e-300), scal)
        for _ in range(8):
            Fz = np.array([sysm.f(t + C[i] * h, z + Z[i]) for i in range(3)])
            MZ = Z @ M.T
            r = -(AINV @ MZ) + h * Fz
            rt = TI @ r
            dW1 = lu_solve(lur, rt[0] / h)
            dW23 = lu_solve(luc, (rt[1] + 1j * rt[2]) / h)
            dW = np.vstack([dW1, dW23.real, dW23.imag])
            dZ = TT @ dW
            Z = Z + dZ
            dn = math.sqrt(np.mean((dZ / scal[None, :]) ** 2))
            if dn_old is not None:
                theta = dn / max(dn_old, 1e-300)
                if theta >= 0.99:
                    break
                if theta / (1 - theta) * dn <= fnewt or dn < 1e-10:
                    converged = True
                    break
            elif dn < 1e-10:
                converged = True
                break
            dn_old = dn
        if trace is not None:
            trace(t, h, converged, theta, dn, z)
        if not converged:
            # a new Jacobian, or a smaller step
            if not fresh_jac:
                J = sysm.jac(t, z)
                fresh_jac = True
                lu = None
                continue
            h *= 0.5
            lu = None
            reject = True
            if h < 1e-14 * max(1.0, abs(span)):
                raise DAEError(f"the step size went to zero at t = {t:g} (Newton does not converge)")
            continue
        # error estimate (RADAU5)
        f0 = sysm.f(t, z)
        F1 = (DD[0] * Z[0] + DD[1] * Z[1] + DD[2] * Z[2]) / h
        cont = lu_solve(lur, f0 + M @ F1)          # (U1/h M - J) = the real Newton matrix
        esc = np.where(i2, scal, ae + rtol * np.maximum(np.abs(z), np.abs(z + Z[2])))
        err = math.sqrt(np.mean((cont / esc) ** 2))
        if err >= 1 and (first or reject):
            cont = lu_solve(lur, sysm.f(t, z + cont) + M @ F1)
            err = math.sqrt(np.mean((cont / esc) ** 2))
        err = max(err, 1e-10)
        fac = min(4.0, max(0.2, 0.9 * err ** -0.25))
        if err < 1:
            steps.append(Dense(t, h, z.copy(), Z.copy()))
            t = t + h
            z = z + Z[2]
            zmax = np.maximum(zmax, np.abs(z))
            first, reject = False, False
            # Gustafsson's step-size control
            if err_old is not None and h_old is not None:
                facgus = (h_old / h) * (err ** 2 / err_old) ** 0.25 / 0.9
                facgus = min(4.0, max(0.2, facgus))
                fac = min(fac, 1 / facgus) if facgus > 0 else fac
            err_old, h_old = max(err, 1e-2), h
            h_new = h * fac
            q = h_new / h
            if theta <= 1e-3 and 1.0 <= q <= 1.2:
                fresh_jac = False                   # RADAU5: keep the step and the factorisation
            else:
                if theta > 1e-3:
                    J = sysm.jac(t, z)
                    fresh_jac = True
                else:
                    fresh_jac = False
                lu = None
                h = h_new
        else:
            reject = True
            h = h * min(1.0, fac)
            lu = None
    return steps, z

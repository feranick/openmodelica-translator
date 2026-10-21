model linearized_model "omct_model"
  parameter Integer n = 2 "number of states";
  parameter Integer m = 1 "number of inputs";
  parameter Integer p = 1 "number of outputs";

  parameter Real x0[n] = {11.99500211661164, 0.0004997929496271882};
  parameter Real u0[m] = {0};

  parameter Real A[n, n] =
	[-0.4166666585668932, 10000.00000000412;
	-100.0000008240895, -1000.000000548096];

  parameter Real B[n, m] =
	[0;
	99.99999880261984];

  parameter Real C[p, n] =
	[1.000000008240895, 0];

  parameter Real D[p, m] =
	[0];


  Real x[n](start=x0);
  input Real u[m](start=u0);
  output Real y[p];

  Real 'x_C.v' = x[1];
  Real 'x_L.i' = x[2];
  Real 'u_u' = u[1];
  Real 'y_y' = y[1];
equation
  der(x) = A * x + B * u;
  y = C * x + D * u;
end linearized_model;

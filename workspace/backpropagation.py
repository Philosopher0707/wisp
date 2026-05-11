"""
Backpropagation from Scratch
=============================
A clean, educational implementation of backpropagation for a feedforward
neural network with one hidden layer, trained on a binary classification task.

Key concepts demonstrated:
  - Forward pass (linear + sigmoid activation)
  - Binary cross-entropy loss
  - Backward pass (chain rule / backpropagation)
  - Gradient descent weight updates
"""

import numpy as np
from typing import Tuple


# ---------------------------------------------------------------------------
# Activation functions & their derivatives
# ---------------------------------------------------------------------------

def sigmoid(z: np.ndarray) -> np.ndarray:
    """Sigmoid activation: σ(z) = 1 / (1 + e^{-z})"""
    return 1.0 / (1.0 + np.exp(-z))


def sigmoid_derivative(a: np.ndarray) -> np.ndarray:
    """Derivative of sigmoid given *post-activation* a: σ'(z) = a * (1 - a)"""
    return a * (1.0 - a)


# ---------------------------------------------------------------------------
# Loss function
# ---------------------------------------------------------------------------

def binary_cross_entropy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Binary cross-entropy loss (averaged over examples).
    L = -1/m * Σ [ y*log(ŷ) + (1-y)*log(1-ŷ) ]
    """
    m = y_true.shape[0]
    # Clip predictions to avoid log(0)
    eps = 1e-12
    y_pred = np.clip(y_pred, eps, 1.0 - eps)
    loss = -np.sum(y_true * np.log(y_pred) + (1.0 - y_true) * np.log(1.0 - y_pred))
    return loss / m


# ---------------------------------------------------------------------------
# Neural Network with backpropagation
# ---------------------------------------------------------------------------

class NeuralNetwork:
    """
    Two-layer feedforward network:
        Input  →  Hidden (sigmoid)  →  Output (sigmoid)

    Parameters
    ----------
    input_dim  : int   Number of input features
    hidden_dim : int   Number of hidden neurons
    """

    def __init__(self, input_dim: int, hidden_dim: int):
        # He initialization for better gradient flow
        self.W1 = np.random.randn(input_dim, hidden_dim) * np.sqrt(2.0 / input_dim)
        self.b1 = np.zeros((1, hidden_dim))
        self.W2 = np.random.randn(hidden_dim, 1) * np.sqrt(2.0 / hidden_dim)
        self.b2 = np.zeros((1, 1))

        # Caches for the backward pass
        self._cache: dict = {}

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def forward(self, X: np.ndarray) -> np.ndarray:
        """
        Forward pass through the network.

        X  shape: (m, input_dim)

        Returns ŷ  shape: (m, 1)
        """
        # Hidden layer
        Z1 = np.dot(X, self.W1) + self.b1          # (m, hidden_dim)
        A1 = sigmoid(Z1)                            # (m, hidden_dim)

        # Output layer
        Z2 = np.dot(A1, self.W2) + self.b2          # (m, 1)
        A2 = sigmoid(Z2)                            # (m, 1)  ← ŷ

        # Store intermediate values for backprop
        self._cache = {"X": X, "Z1": Z1, "A1": A1, "Z2": Z2, "A2": A2}

        return A2

    # ------------------------------------------------------------------
    # Backward pass  (backpropagation)
    # ------------------------------------------------------------------

    def backward(self, y_true: np.ndarray) -> dict:
        """
        Compute gradients of the loss w.r.t. all parameters via the chain rule.

        Returns a dict of gradients:
            dW1, db1, dW2, db2
        """
        m = y_true.shape[0]                         # number of examples
        cache = self._cache

        X   = cache["X"]                            # (m, input_dim)
        A1  = cache["A1"]                           # (m, hidden_dim)
        A2  = cache["A2"]                           # (m, 1)  ← ŷ

        # ---- Output layer gradients ----
        # dL/dZ2  =  ŷ - y   (nice property of BCE + sigmoid)
        dZ2 = A2 - y_true                           # (m, 1)

        dW2 = np.dot(A1.T, dZ2) / m                 # (hidden_dim, 1)
        db2 = np.sum(dZ2, axis=0, keepdims=True) / m  # (1, 1)

        # ---- Hidden layer gradients ----
        # dL/dA1 = dZ2 · W2^T
        dA1 = np.dot(dZ2, self.W2.T)                # (m, hidden_dim)

        # dL/dZ1 = dL/dA1  ⊙  σ'(Z1)
        dZ1 = dA1 * sigmoid_derivative(A1)          # (m, hidden_dim)

        dW1 = np.dot(X.T, dZ1) / m                  # (input_dim, hidden_dim)
        db1 = np.sum(dZ1, axis=0, keepdims=True) / m  # (1, hidden_dim)

        return {"dW1": dW1, "db1": db1, "dW2": dW2, "db2": db2}

    # ------------------------------------------------------------------
    # Parameter update  (gradient descent)
    # ------------------------------------------------------------------

    def update(self, grads: dict, learning_rate: float) -> None:
        """Apply gradient descent:  θ ← θ - η * ∇θ"""
        self.W1 -= learning_rate * grads["dW1"]
        self.b1 -= learning_rate * grads["db1"]
        self.W2 -= learning_rate * grads["dW2"]
        self.b2 -= learning_rate * grads["db2"]

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        epochs: int = 1000,
        learning_rate: float = 0.1,
        verbose: bool = True,
    ) -> list:
        """
        Full training loop: forward → loss → backward → update.

        Returns a list of loss values per epoch.
        """
        y = y.reshape(-1, 1)                        # ensure column vector
        losses = []

        for epoch in range(epochs):
            # Forward
            y_pred = self.forward(X)

            # Loss
            loss = binary_cross_entropy(y, y_pred)
            losses.append(loss)

            # Backward
            grads = self.backward(y)

            # Update
            self.update(grads, learning_rate)

            # Logging
            if verbose and epoch % 200 == 0:
                acc = self.accuracy(X, y)
                print(f"Epoch {epoch:4d}  |  Loss: {loss:.6f}  |  Accuracy: {acc:.2%}")

        return losses

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Binary predictions (threshold 0.5)."""
        return (self.forward(X) >= 0.5).astype(int)

    def accuracy(self, X: np.ndarray, y: np.ndarray) -> float:
        """Classification accuracy."""
        y = y.reshape(-1, 1)
        return float(np.mean(self.predict(X) == y))


# ---------------------------------------------------------------------------
# Demo: XOR problem
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # XOR dataset — a classic non-linearly-separable problem
    X = np.array([
        [0, 0],
        [0, 1],
        [1, 0],
        [1, 1],
    ], dtype=float)

    y = np.array([0, 1, 1, 0], dtype=float)         # XOR truth table

    # Build and train
    np.random.seed(42)
    nn = NeuralNetwork(input_dim=2, hidden_dim=4)

    print("Training on XOR problem\n" + "-" * 45)
    losses = nn.train(X, y, epochs=2000, learning_rate=0.5)

    # Final results
    print("-" * 45)
    print("\nFinal predictions:")
    for i, row in enumerate(X):
        pred = nn.forward(row.reshape(1, -1))[0, 0]
        print(f"  XOR({int(row[0])}, {int(row[1])}) = {int(y[i])}  "
              f"→  ŷ = {pred:.4f}  ({'1' if pred >= 0.5 else '0'})")

    print(f"\nFinal accuracy: {nn.accuracy(X, y):.2%}")

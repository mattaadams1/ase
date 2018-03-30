""" Methods for generating new random starting candidates. 
If you find this implementation useful in your work,
please cite:
    M. Van den Bossche, Henrik Gronbeck, B. Hammer,
      TO BE PUBLISHED
in addition to the papers mentioned in the docstrings."""
from random import shuffle, random, sample
import numpy as np
from ase import Atoms
from ase.data import atomic_numbers
from ase.build import molecule
from ase.ga.utilities import closest_distances_generator
from ase.ga.bulk_utilities import atoms_too_close


def random_pos(cell):
    """ Returns a random position within the box
        described by the input box. """
    pos = np.zeros(3)
    for i in range(3):
        pos += random() * cell[i, :]
    return pos


class StartGenerator(object):
    """ Class used to generate random starting candidates of bulk 
    structures. The candidates are generated by:
    - randomly creating a cell that satisfies the specified volume
    - adding one atom at a time, respecting the minimal distances

    Parameters:

    blocks: list of building units for the structure, each item
            as a (A, B) tuple, with A either an Atoms object or
            string (element or molecule name) and B the number
            of these repeating units. 

    blmin: A dictionary with minimal interatomic distances. If
           an integer is provided instead, the dictionary will be 
           generated with this fraction of covalent bond lengths.

    volume: initial guess for the unit cell volume

    cellbounds: CellBounds instance that specifies limits on cell shape

    cell: if you want to keep the cell vectors fixed, the desired
          cell is to be specified here.

    splits: splitting scheme to be used, based on:
    Lyakhov, Oganov, Valle, Comp. Phys. Comm. 181 (2010) 1623-32
    This should be a dict specifying the relative probabilities for each
    split, written as tuples. As an example, (4,2) means split factors 
    of 4 and 2 for two directions (randomly chosen), with no splitting
    along the remaining direction.
    """

    def __init__(self, blocks, blmin, volume, cellbounds=None,
                 cell=None, splits={(1,): 1}):

        self.volume = volume
        self.cellbounds = cellbounds
        self.cell = cell

        # normalize splitting probabilities:
        tot = sum([v for v in splits.values()])
        self.splits = {k: v * 1. / tot for k, v in splits.iteritems()}

        # pre-process blocks and blmin:
        self.blocks = []
        numbers = []
        for block, count in blocks:
            if isinstance(block, Atoms):
                pass
            elif block in atomic_numbers:
                block = Atoms(block)
            else:
                block = molecule(block)
            self.blocks.append((block, count))
            numbers.extend(block.get_atomic_numbers())
        numbers = list(set(numbers))  # unique atomic numbers

        if type(blmin) == dict:
            self.blmin = blmin
        else:
            self.blmin = closest_distances_generator(numbers, blmin)

    def get_new_candidate(self):
        """ Returns a new candidate. """

        pbc = [True] * 3
        blmin = self.blmin

        # generating the cell
        # cell splitting:
        # choose factors according to the probabilities
        r = random()
        cumprob = 0
        for split, prob in self.splits.iteritems():
            cumprob += prob
            if cumprob > r:
                break

        directions = sample(range(3), len(split))
        repeat = [1, 1, 1]
        for i, d in enumerate(directions):
            repeat[d] = split[i]
        repeat = tuple(repeat)

        nparts = np.product(repeat)
        target_volume = self.volume / nparts

        # Randomly create a cell; without loss of generality,
        # a lower triangular form can be used, with tilt factors
        # within certain bounds.
        # For a cell to be valid, the full cell has to satisfy
        # the cellbounds constraints. Additionally, the length of
        # each subcell vector has to be greater than the largest
        # (X,X)-minimal-interatomic-distance in blmin.

        if self.cell is not None:
            full_cell = np.copy(self.cell)
            cell = (full_cell.T / repeat).T
            valid = True
        else:
            valid = False

        while not valid:
            blminmax = max([blmin[k] for k in blmin if k[0] == k[1]])
            cell = np.zeros((3, 3))
            l = target_volume**0.33
            cell[0, 0] = random() * l
            cell[1, 0] = (random() - 0.5) * cell[0, 0]
            cell[1, 1] = random() * l
            cell[2, 0] = (random() - 0.5) * cell[0, 0]
            cell[2, 1] = (random() - 0.5) * cell[1, 1]
            cell[2, 2] = random() * l

            volume = abs(np.linalg.det(cell))
            cell *= (target_volume / volume)**0.33

            full_cell = (repeat * cell.T).T

            valid = True
            if self.cellbounds is not None:
                if not self.cellbounds.is_within_bounds(full_cell):
                    valid = False
            for i in range(3):
                if np.linalg.norm(cell[i, :]) < blminmax:
                    valid = False

        # generating the atomic positions
        blocks = []
        surplus = []
        indices = []
        for i, (block, count) in enumerate(self.blocks):
            count_part = int(np.ceil(count * 1. / nparts))
            surplus.append(nparts * count_part - count)
            blocks.extend([block] * count_part)
            indices.extend([i] * count_part)

        N_blocks = len(blocks)

        # The ordering is shuffled so different blocks
        # are added in random order.
        order = range(N_blocks)
        shuffle(order)
        blocks = [blocks[i] for i in order]
        indices = np.array(indices)[order]

        # Runs until we have found a valid candidate.
        while True:
            cand = Atoms('', cell=cell, pbc=pbc)
            cand_list = []
            # Make each new position one at a time.
            for i in range(N_blocks):

                atoms = blocks[i].copy()
                atoms.set_tags(i)
                rotate = len(atoms) > 1

                pos_found = False
                while not pos_found:
                    cop = atoms.get_positions().mean(axis=0)
                    pos = random_pos(cell)
                    atoms.translate(pos - cop)
                    if rotate:
                        phi, theta, psi = 360 * np.random.random(3)
                        atoms.euler_rotate(phi=phi, theta=0.5 * theta, psi=psi,
                                           center=pos)
                    # add if it fits:
                    attempt = cand + atoms
                    attempt.wrap()
                    too_close = atoms_too_close(attempt, blmin, use_tags=True)
                    if not too_close:
                        cand += atoms
                        cand_list.append(atoms)
                        break

            # rebuild the candidate after repeating,
            # randomly deleting surplus blocks and
            # sorting back to the original order
            tags = cand.get_tags()
            nrep = int(np.prod(repeat))
            cand_full = cand.repeat(repeat)

            tags_full = cand_full.get_tags()
            for i in range(nrep):
                tags_full[len(cand) * i:len(cand) * (i + 1)] += i * N_blocks
            cand_full.set_tags(tags_full)

            cand = Atoms('', cell=full_cell, pbc=pbc)
            indices_full = np.tile(indices, nrep)
            tag_counter = 0
            for i, (block, count) in enumerate(self.blocks):
                tags = np.where(indices_full == i)[0]
                bad = np.random.choice(tags, size=surplus[i], replace=False)
                for tag in tags:
                    if tag not in bad:
                        select = [a.index for a in cand_full if a.tag == tag]
                        atoms = cand_full[select]  # is indeed a copy!
                        atoms.set_tags(tag_counter)
                        assert len(atoms) == len(block)
                        cand += atoms
                        tag_counter += 1

            break

        return cand

from __future__ import print_function
import os
import sys
import weakref
import pickle
import subprocess
from gettext import gettext as _, ngettext

import numpy as np

from ase import __version__
import ase.gui.backend as be
from ase.gui.calculator import SetCalculator
from ase.gui.crystal import SetupBulkCrystal
from ase.gui.defaults import read_defaults
from ase.gui.energyforces import EnergyForces
from ase.gui.graphene import SetupGraphene
from ase.gui.minimize import Minimize
from ase.gui.nanoparticle import SetupNanoparticle
from ase.gui.nanotube import SetupNanotube
from ase.gui.quickinfo import QuickInfo
from ase.gui.save import save_dialog
from ase.gui.scaling import HomogeneousDeformation
from ase.gui.settings import Settings
from ase.gui.status import Status
from ase.gui.surfaceslab import SetupSurfaceSlab
from ase.gui.view import View
from ase.gui.widgets import pack, oops


class GUI(View, Status):
    def __init__(self, images,
                 rotations='',
                 show_unit_cell=True,
                 show_bonds=False):

        # Try to change into directory of file you are viewing
        try:
            os.chdir(os.path.split(sys.argv[1])[0])
        # This will fail sometimes (e.g. for starting a new session)
        except:
            pass

        self.images = images

        self.config = read_defaults()

        menu = self.get_menu_data(show_unit_cell, show_bonds)

        self.window = be.MainWindow(menu, self.config,
                                    self.exit, self.scroll,
                                    self.scroll_event,
                                    self.press, self.move, self.release)

        View.__init__(self, rotations)
        Status.__init__(self)

        self.graphs = []  # list of matplotlib processes
        self.graph_wref = []  # list of weakrefs to Graph objects
        self.movie_window = None
        self.vulnerable_windows = []
        self.simulation = {}  # Used by modules on Calculate menu.
        self.module_state = {}  # Used by modules to store their state.

    def run(self, expr=None):
        self.set_colors()
        self.set_coordinates(self.images.nimages - 1, focus=True)

        if self.images.nimages > 1:
            self.movie()

        if expr is None and not np.isnan(self.images.E[0]):
            expr = self.config['gui_graphs_string']

        if expr is not None and expr != '' and self.images.nimages > 1:
            self.plot_graphs(expr=expr)

        self.window.run()

    def step(self, action):
        d = {'First': -10000000,
             'Previous': -1,
             'Next': 1,
             'Last': 10000000}[action.get_name()]
        i = max(0, min(self.images.nimages - 1, self.frame + d))
        self.set_frame(i)
        if self.movie_window is not None:
            self.movie_window.frame_number.value = i

    def _do_zoom(self, x):
        """Utility method for zooming"""
        self.scale *= x
        self.draw()

    def zoom(self, action):
        """Zoom in/out on keypress or clicking menu item"""
        x = {'ZoomIn': 1.2, 'ZoomOut': 1 / 1.2}[action.get_name()]
        self._do_zoom(x)

    def scroll_event(self, window, event):
        """Zoom in/out when using mouse wheel"""
        SHIFT = event.state == gtk.gdk.SHIFT_MASK
        x = 1.0
        if event.direction == gtk.gdk.SCROLL_UP:
            x = 1.0 + (1 - SHIFT) * 0.2 + SHIFT * 0.01
        elif event.direction == gtk.gdk.SCROLL_DOWN:
            x = 1.0 / (1.0 + (1 - SHIFT) * 0.2 + SHIFT * 0.01)
        self._do_zoom(x)

    def settings(self, menuitem):
        Settings(self)

    def scroll(self, window, event):
        from copy import copy
        CTRL = event.state == gtk.gdk.CONTROL_MASK
        SHIFT = event.state == gtk.gdk.SHIFT_MASK
        dxdydz = {
            gtk.keysyms.KP_Add: ('zoom', 1.0 +
                                 (1 - SHIFT) * 0.2 + SHIFT * 0.01, 0),
            gtk.keysyms.KP_Subtract: ('zoom', 1 /
                                      (1.0 +
                                       (1 - SHIFT) * 0.2 + SHIFT * 0.01), 0),
            gtk.keysyms.Up: (0, +1 - CTRL, +CTRL),
            gtk.keysyms.Down: (0, -1 + CTRL, -CTRL),
            gtk.keysyms.Right: (+1, 0, 0),
            gtk.keysyms.Left: (-1, 0, 0)
        }.get(event.keyval, None)

        sel = []

        atom_move = self.ui.get_widget(
            '/MenuBar/ToolsMenu/MoveAtoms').get_active()
        atom_rotate = self.ui.get_widget(
            '/MenuBar/ToolsMenu/RotateAtoms').get_active()
        atom_orient = self.ui.get_widget(
            '/MenuBar/ToolsMenu/OrientAtoms').get_active()
        if dxdydz is None:
            return
        dx, dy, dz = dxdydz
        if dx == 'zoom':
            self._do_zoom(dy)
            return

        tvec = np.array([dx, dy, dz])

        dir_vec = np.dot(self.axes, tvec)
        if (atom_move):
            rotmat = self.axes
            s = 0.1
            if SHIFT:
                s = 0.01
            add = s * dir_vec
            for i in range(len(self.R)):
                if self.atoms_to_rotate_0[i]:
                    self.R[i] += add
                    for jx in range(self.images.nimages):
                        self.images.P[jx][i] += add
        elif atom_rotate:
            from .rot_tools import rotate_about_vec, rotate_vec
            sel = self.images.selected
            if sum(sel) == 0:
                sel = self.atoms_to_rotate_0
            nsel = sum(sel)
            # this is the first one to get instatiated
            if nsel != 2:
                self.rot_vec = dir_vec

            change = False
            z_axis = np.dot(self.axes, np.array([0, 0, 1]))
            if self.atoms_to_rotate is None:
                change = True
                self.z_axis_old = z_axis.copy()
                self.dx_change = [0, 0]
                self.atoms_to_rotate = self.atoms_to_rotate_0.copy()
                self.atoms_selected = sel.copy()
                self.rot_vec = dir_vec

            if nsel != 2 or sum(self.atoms_to_rotate) == 2:
                self.dx_change = [0, 0]

            for i in range(len(sel)):
                if sel[i] != self.atoms_selected[i]:
                    change = True
            cz = [dx, dy + dz]

            if cz[0] or cz[1]:
                change = False
            if not (cz[0] * (self.dx_change[1])):
                change = True
            for i in range(2):
                if cz[i] and self.dx_change[i]:
                    self.rot_vec = self.rot_vec * cz[i] * self.dx_change[i]
                    if cz[1]:
                        change = False

            if np.prod(self.z_axis_old != z_axis):
                change = True
            self.z_axis_old = z_axis.copy()
            self.dx_change = copy(cz)
            dihedral_rotation = len(self.images.selected_ordered) == 4

            if change:
                self.atoms_selected = sel.copy()

                if nsel == 2 and sum(self.atoms_to_rotate) != 2:
                    asel = []
                    for i, j in enumerate(sel):
                        if j:
                            asel.append(i)
                    a1, a2 = asel

                    rvx = (self.images.P[self.frame][a1] -
                           self.images.P[self.frame][a2])

                    rvy = np.cross(rvx, np.dot(self.axes, np.array([0, 0, 1])))
                    self.rot_vec = rvx * dx + rvy * (dy + dz)
                    self.dx_change = [dx, dy + dz]

                    # dihedral rotation?
                if dihedral_rotation:
                    sel = self.images.selected_ordered
                    self.rot_vec = (dx + dy + dz) * (
                        self.R[sel[2]] - self.R[sel[1]])

            rot_cen = np.array([0.0, 0.0, 0.0])
            if dihedral_rotation:
                sel = self.images.selected_ordered
                rot_cen = self.R[sel[1]].copy()
            elif nsel:
                for i, b in enumerate(sel):
                    if b:
                        rot_cen += self.R[i]
                rot_cen /= float(nsel)

            degrees = 5 * (1 - SHIFT) + SHIFT
            degrees = abs(sum(dxdydz)) * 3.1415 / 360.0 * degrees
            rotmat = rotate_about_vec(self.rot_vec, degrees)

            # now rotate the atoms that are to be rotated
            for i in range(len(self.R)):
                if self.atoms_to_rotate[i]:
                    self.R[i] -= rot_cen
                    for jx in range(self.images.nimages):
                        self.images.P[jx][i] -= rot_cen

                    self.R[i] = rotate_vec(rotmat, self.R[i])
                    for jx in range(self.images.nimages):
                        self.images.P[jx][i] = rotate_vec(rotmat,
                                                          self.images.P[jx][i])

                    self.R[i] += rot_cen
                    for jx in range(self.images.nimages):
                        self.images.P[jx][i] += rot_cen
        elif atom_orient:
            to_vec = np.array([dx, dy, dz])

            from .rot_tools import rotate_vec_into_newvec
            rot_mat = rotate_vec_into_newvec(self.orient_normal, to_vec)
            self.axes = rot_mat

            self.set_coordinates()
        else:
            self.center -= (
                dx * 0.1 * self.axes[:, 0] - dy * 0.1 * self.axes[:, 1]
            )
        self.draw()

    def copy_atoms(self, widget):
        "Copies selected atoms to a clipboard."

        clip = gtk.clipboard_get(gtk.gdk.SELECTION_CLIPBOARD)

        if self.images.selected.any():
            atoms = self.images.get_atoms(self.frame)
            lena = len(atoms)
            for i in range(len(atoms)):
                li = lena - 1 - i
                if not self.images.selected[li]:
                    del (atoms[li])
            for i in atoms:
                i.position = np.dot(self.axes.T, i.position)
            ref = atoms[0].position
            for i in atoms:
                if i.position[2] < ref[2]:
                    ref = i.position
            atoms.reference_position = ref
            clip.set_text(pickle.dumps(atoms, 0))

    def paste_atoms(self, widget):
        """Inserts clipboard selection into the current frame using the
        add_atoms window."""
        clip = gtk.clipboard_get(gtk.gdk.SELECTION_CLIPBOARD)
        try:
            atoms = pickle.loads(clip.wait_for_text())
        except TypeError:
            pass
        else:
            self.add_atoms(widget, data='Paste', paste=atoms)

    def add_atoms(self, widget, data=None, paste=None):
        """Presents a dialogbox to the user, that allows him to add
        atoms/molecule to the current slab or to paste the clipboard.

        The molecule/atom is rotated using the current rotation of the
        coordinate system.

        The molecule/atom can be added at a specified position - if the
        keyword auto+Z is used, the COM of the selected atoms will be used
        as COM for the moleculed. The COM is furthermore
        translated Z ang towards the user.

        If no molecules are selected, the COM of all the atoms will be used
        for the x-y components of the active coordinate system, while the
        z-direction will be chosen from the nearest atom position
        along this direction.

        Note: If this option is used, all frames except the active one are
        deleted.
        """

        if data == 'load':
            chooser = gtk.FileChooserDialog(
                _('Open ...'), None, gtk.FILE_CHOOSER_ACTION_OPEN,
                (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN,
                 gtk.RESPONSE_OK))

            chooser.set_filename(_("<<filename>>"))
            ok = chooser.run()
            if ok == gtk.RESPONSE_OK:
                filename = chooser.get_filename()
                chooser.destroy()
            else:
                chooser.destroy()
                return

        if data == 'OK' or data == 'load':
            import ase
            if data == 'load':
                molecule = filename
            else:
                molecule = self.add_entries[1].get_text()
            tag = self.add_entries[2].get_text()
            mom = self.add_entries[3].get_text()
            pos = self.add_entries[4].get_text().lower()

            if paste is not None:
                a = paste.copy()
            else:
                a = None

            if a is None:
                try:
                    a = ase.Atoms([ase.Atom(molecule)])
                except:
                    try:
                        import ase.build
                        a = ase.build.molecule(molecule)
                    except:
                        try:
                            a = ase.io.read(molecule, -1)
                        except:
                            self.add_entries[1].set_text('?' + molecule)
                            return ()

            directions = np.transpose(self.axes)
            if a is not None:
                for i in a:
                    try:
                        i.set('tag', int(tag))
                    except:
                        self.add_entries[2].set_text('?' + tag)
                        return ()
                    try:
                        i.magmom = float(mom)
                    except:
                        self.add_entries[3].set_text('?' + mom)
                        return ()
                if self.origin_radio.get_active() and paste:
                    a.translate(-paste.reference_position)
                # apply the current rotation matrix to A
                for i in a:
                    i.position = np.dot(self.axes, i.position)
                # find the extent of the molecule in the local coordinate
                # system
                if self.centre_radio.get_active():
                    a_cen_pos = np.array([0.0, 0.0, 0.0])
                    m_cen_pos = 0.0
                    for i in a.positions:
                        a_cen_pos[0] += np.dot(directions[0], i)
                        a_cen_pos[1] += np.dot(directions[1], i)
                        a_cen_pos[2] += np.dot(directions[2], i)
                        m_cen_pos = max(np.dot(-directions[2], i), m_cen_pos)

                    a_cen_pos[0] /= len(a.positions)
                    a_cen_pos[1] /= len(a.positions)
                    a_cen_pos[2] /= len(a.positions)
                    a_cen_pos[2] -= m_cen_pos
                else:
                    a_cen_pos = np.array([0.0, 0.0, 0.0])

                # now find the position
                cen_pos = np.array([0.0, 0.0, 0.0])
                if sum(self.images.selected) > 0:
                    for i in range(len(self.R)):
                        if self.images.selected[i]:
                            cen_pos += self.R[i]
                    cen_pos /= sum(self.images.selected)
                elif len(self.R) > 0:
                    px = 0.0
                    py = 0.0
                    pz = -1e6

                    for i in range(len(self.R)):
                        px += np.dot(directions[0], self.R[i])
                        py += np.dot(directions[1], self.R[i])
                        pz = max(np.dot(directions[2], self.R[i]), pz)
                    px = (px / float(len(self.R)))
                    py = (py / float(len(self.R)))
                    cen_pos = (directions[0] * px +
                               directions[1] * py +
                               directions[2] * pz)

                if 'auto' in pos:
                    pos = pos.replace('auto', '')
                    import re
                    pos = re.sub('\s', '', pos)
                    if '(' in pos:
                        sign = eval('%s1' % pos[0])
                        a_cen_pos -= sign * np.array(eval(pos[1:]), float)
                    else:
                        a_cen_pos -= float(pos) * directions[2]
                else:
                    cen_pos = np.array(eval(pos))
                for i in a:
                    i.position += cen_pos - a_cen_pos

            # and them to the molecule
                atoms = self.images.get_atoms(self.frame)
                atoms = atoms + a
                self.new_atoms(atoms, init_magmom=True)

                # and finally select the new molecule for easy moving and
                # rotation
                for i in range(len(a)):
                    self.images.selected[len(atoms) - i - 1] = True

                self.draw()
            self.add_entries[0].destroy()

        if data == 'Cancel':
            self.add_entries[0].destroy()

        if data is None or data == 'Paste':
            from ase.gui.widgets import pack
            molecule = ''
            tag = '0'
            mom = '0'
            pos = 'auto+1'
            self.add_entries = []
            window = gtk.Window(gtk.WINDOW_TOPLEVEL)
            self.add_entries.append(window)
            window.set_title(_('Add atoms'))
            if data == 'Paste':
                molecule = paste.get_chemical_formula()
                window.set_title(_('Paste'))

            vbox = gtk.VBox(False, 0)
            window.add(vbox)
            vbox.show()
            packed = False
            for i, j in [[_('Insert atom or molecule'), molecule],
                         [_('Tag'), tag], [_('Moment'), mom], [_('Position'),
                                                               pos]]:

                label = gtk.Label(i)
                if not packed:
                    vbox.pack_start(label, True, True, 0)
                else:
                    packed = True
                    vbox.add(label)
                label.show()

                entry = gtk.Entry()
                entry.set_text(j)
                self.add_entries.append(entry)
                entry.set_max_length(50)
                entry.show()
                vbox.add(entry)

            pack(vbox, [gtk.Label('atom/molecule reference:')])
            self.centre_radio = gtk.RadioButton(None, "centre ")
            self.origin_radio = gtk.RadioButton(self.centre_radio, "origin")
            pack(vbox, [self.centre_radio, self.origin_radio])
            if data == 'Paste':
                self.origin_radio.set_active(True)
                self.add_entries[1].set_sensitive(False)
            if data is None:
                button = gtk.Button(_('_Load molecule'))
                button.connect('clicked', self.add_atoms, 'load')
                button.show()
                vbox.add(button)
            button = gtk.Button(_('_OK'))
            button.connect('clicked', self.add_atoms, 'OK', paste)
            button.show()
            vbox.add(button)
            button = gtk.Button(_('_Cancel'))
            button.connect('clicked', self.add_atoms, 'Cancel')
            button.show()
            vbox.add(button)
            window.show()

    def modify_atoms(self, widget, data=None):
        """Presents a dialog box where the user is able to change the
        atomic type, the magnetic moment and tags of the selected atoms.
        An item marked with X will not be changed.
        """
        if data:
            if data == 'OK':
                import ase
                symbol = self.add_entries[1].get_text()
                tag = self.add_entries[2].get_text()
                mom = self.add_entries[3].get_text()
                a = None
                if symbol != 'X':
                    try:
                        a = ase.Atoms([ase.Atom(symbol)])
                    except:
                        self.add_entries[1].set_text('?' + symbol)
                        return ()

                y = self.images.selected.copy()
                # and them to the molecule
                atoms = self.images.get_atoms(self.frame)
                for i in range(len(atoms)):
                    if self.images.selected[i]:
                        if a:
                            atoms[i].symbol = symbol
                        try:
                            if tag != 'X':
                                atoms[i].tag = int(tag)
                        except:
                            self.add_entries[2].set_text('?' + tag)
                            return ()
                        try:
                            if mom != 'X':
                                atoms[i].magmom = float(mom)
                        except:
                            self.add_entries[3].set_text('?' + mom)
                            return ()
                self.new_atoms(atoms, init_magmom=True)

                # Updates atomic labels
                a = self.ui.get_action_groups()[0].get_action("NoLabel")
                cv = a.get_current_value()
                a.set_current_value(0)
                a.set_current_value(cv)

                # and finally select the new molecule for easy moving
                # and rotation
                self.images.selected = y
                self.draw()

            self.add_entries[0].destroy()
        if data is None and sum(self.images.selected):
            atoms = self.images.get_atoms(self.frame)
            s_tag = ''
            s_mom = ''
            s_symbol = ''
            # Get the tags, moments and symbols of the selected atomsa
            for i in range(len(atoms)):
                if self.images.selected[i]:
                    if not (s_tag):
                        s_tag = str(atoms[i].tag)
                    elif s_tag != str(atoms[i].tag):
                        s_tag = 'X'
                    if not (s_mom):
                        s_mom = ("%2.2f" % (atoms[i].magmom))
                    elif s_mom != ("%2.2f" % (atoms[i].magmom)):
                        s_mom = 'X'
                    if not (s_symbol):
                        s_symbol = str(atoms[i].symbol)
                    elif s_symbol != str(atoms[i].symbol):
                        s_symbol = 'X'

            self.add_entries = []
            window = gtk.Window(gtk.WINDOW_TOPLEVEL)
            self.add_entries.append(window)
            window.set_title(_('Modify'))

            vbox = gtk.VBox(False, 0)
            window.add(vbox)
            vbox.show()
            pack = False
            for i, j in [[_('Atom'), s_symbol], [_('Tag'), s_tag],
                         [_('Moment'), s_mom]]:
                label = gtk.Label(i)
                if not pack:
                    vbox.pack_start(label, True, True, 0)
                else:
                    pack = True
                    vbox.add(label)
                label.show()

                entry = gtk.Entry()
                entry.set_text(j)
                self.add_entries.append(entry)
                entry.set_max_length(50)
                entry.show()
                vbox.add(entry)
            button = gtk.Button(_('_OK'))
            button.connect('clicked', self.modify_atoms, 'OK')
            button.show()
            vbox.add(button)
            button = gtk.Button(_('_Cancel'))
            button.connect('clicked', self.modify_atoms, 'Cancel')
            button.show()
            vbox.add(button)

            window.show()

    def delete_selected_atoms(self, widget=None, data=None):
        if data == 'OK':
            atoms = self.images.get_atoms(self.frame)
            lena = len(atoms)
            for i in range(len(atoms)):
                li = lena - 1 - i
                if self.images.selected[li]:
                    del (atoms[li])
            self.new_atoms(atoms)

            self.draw()
        if data:
            self.delete_window.destroy()

        if not (data) and sum(self.images.selected):
            nselected = sum(self.images.selected)
            self.delete_window = gtk.Window(gtk.WINDOW_TOPLEVEL)
            self.delete_window.set_title(_('Confirmation'))
            self.delete_window.set_border_width(10)
            self.box1 = gtk.HBox(False, 0)
            self.delete_window.add(self.box1)
            self.button1 = gtk.Button(ngettext('Delete selected atom?',
                                               'Delete selected atoms?',
                                               nselected))
            self.button1.connect("clicked", self.delete_selected_atoms, "OK")
            self.box1.pack_start(self.button1, True, True, 0)
            self.button1.show()

            self.button2 = gtk.Button(_("Cancel"))
            self.button2.connect("clicked", self.delete_selected_atoms,
                                 "Cancel")
            self.box1.pack_start(self.button2, True, True, 0)
            self.button2.show()

            self.box1.show()
            self.delete_window.show()

    def debug(self, x):
        from ase.gui.debug import Debug
        Debug(self)

    def execute(self, widget=None):
        from ase.gui.execute import Execute
        Execute(self)

    def constraints_window(self, widget=None):
        from ase.gui.constraints import Constraints
        Constraints(self)

    def select_all(self, widget):
        self.images.selected[:] = True
        self.draw()

    def invert_selection(self, widget):
        self.images.selected[:] = ~self.images.selected
        self.draw()

    def select_constrained_atoms(self, widget):
        self.images.selected[:] = ~self.images.dynamic
        self.draw()

    def select_immobile_atoms(self, widget):
        if self.images.nimages > 1:
            R0 = self.images.P[0]
            for R in self.images.P[1:]:
                self.images.selected[:] = ~(np.abs(R - R0) > 1.0e-10).any(1)
        self.draw()

    def movie(self, widget=None):
        from ase.gui.movie import Movie
        self.movie_window = Movie(self)

    def plot_graphs(self, x=None, expr=None):
        from ase.gui.graphs import Graphs
        g = Graphs(self)
        if expr is not None:
            g.plot(expr=expr)
        self.graph_wref.append(weakref.ref(g))

    def plot_graphs_newatoms(self):
        "Notify any Graph objects that they should make new plots."
        new_wref = []
        found = 0
        for wref in self.graph_wref:
            ref = wref()
            if ref is not None:
                ref.plot()
                new_wref.append(wref)  # Preserve weakrefs that still work.
                found += 1
        self.graph_wref = new_wref
        return found

    def NEB(self, action):
        from ase.gui.neb import NudgedElasticBand
        NudgedElasticBand(self.images)

    def bulk_modulus(self, action):
        process = subprocess.Popen([sys.executable, '-m', 'ase.eos',
                                    '--plot', '-'],
                                   stdin=subprocess.PIPE)
        v = np.array([abs(np.linalg.det(A)) for A in self.images.A])
        e = self.images.E
        pickle.dump((v, e), process.stdin)
        process.stdin.close()
        self.graphs.append(process)

    def open(self, button=None):
        from ase.io.formats import all_formats, get_ioformat
        formats = [(_('Automatic'), None)]

        def key(item):
            return item[1][0]

        for format, (description, code) in sorted(all_formats.items(),
                                                  key=key):
            io = get_ioformat(format)
            if io.read and description != '?':
                formats.append((_(description), format))

        chooser = gtk.FileChooserDialog(
            _('Open ...'), None, gtk.FILE_CHOOSER_ACTION_OPEN,
            (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN,
             gtk.RESPONSE_OK))
        chooser.set_filename(_("<<filename>>"))

        # Add a file type filter
        name_to_format = {}
        types = gtk.combo_box_new_text()
        for name, format in formats:
            types.append_text(name)
            name_to_format[name] = format

        types.set_active(0)
        img_vbox = gtk.VBox()
        pack(img_vbox, [gtk.Label(_('File type:')), types])
        img_vbox.show()
        chooser.set_extra_widget(img_vbox)

        ok = chooser.run() == gtk.RESPONSE_OK
        if ok:
            filenames = [chooser.get_filename()]
            filetype = types.get_active_text()
        chooser.destroy()

        if not ok:
            return

        self.reset_tools_modes()
        self.images.read(filenames, slice(None), name_to_format[filetype])
        self.set_colors()
        self.set_coordinates(self.images.nimages - 1, focus=True)

    def import_atoms(self, button=None, filenames=None):
        if filenames is None:
            chooser = gtk.FileChooserDialog(
                _('Open ...'), None, gtk.FILE_CHOOSER_ACTION_OPEN,
                (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN,
                 gtk.RESPONSE_OK))
            ok = chooser.run()
            if ok == gtk.RESPONSE_OK:
                filenames = [chooser.get_filename()]
            chooser.destroy()

            if not ok:
                return

        self.images.import_atoms(filenames, self.frame)
        self.set_colors()
        self.set_coordinates(self.images.nimages - 1, focus=True)

    def quick_info_window(self, menuitem):
        QuickInfo(self)

    def bulk_window(self, menuitem):
        SetupBulkCrystal(self)

    def surface_window(self, menuitem):
        SetupSurfaceSlab(self)

    def nanoparticle_window(self, menuitem):
        SetupNanoparticle(self)

    def graphene_window(self, menuitem):
        SetupGraphene(self)

    def nanotube_window(self, menuitem):
        SetupNanotube(self)

    def calculator_window(self, menuitem):
        SetCalculator(self)

    def energy_window(self, menuitem):
        EnergyForces(self)

    def energy_minimize_window(self, menuitem):
        Minimize(self)

    def scaling_window(self, menuitem):
        HomogeneousDeformation(self)

    def new_atoms(self, atoms, init_magmom=False):
        "Set a new atoms object."
        self.reset_tools_modes()

        rpt = getattr(self.images, 'repeat', None)
        self.images.repeat_images(np.ones(3, int))
        self.images.initialize([atoms], init_magmom=init_magmom)
        self.frame = 0  # Prevent crashes
        self.images.repeat_images(rpt)
        self.set_colors()
        self.set_coordinates(frame=0, focus=True)
        self.notify_vulnerable()

    def prepare_new_atoms(self):
        "Marks that the next call to append_atoms should clear the images."
        self.images.prepare_new_atoms()

    def append_atoms(self, atoms):
        "Set a new atoms object."
        # self.notify_vulnerable()   # Do this manually after last frame.
        frame = self.images.append_atoms(atoms)
        self.set_coordinates(frame=frame - 1, focus=True)

    def notify_vulnerable(self):
        """Notify windows that would break when new_atoms is called.

        The notified windows may adapt to the new atoms.  If that is not
        possible, they should delete themselves.
        """
        new_vul = []  # Keep weakrefs to objects that still exist.
        for wref in self.vulnerable_windows:
            ref = wref()
            if ref is not None:
                new_vul.append(wref)
                ref.notify_atoms_changed()
        self.vulnerable_windows = new_vul

    def register_vulnerable(self, obj):
        """Register windows that are vulnerable to changing the images.

        Some windows will break if the atoms (and in particular the
        number of images) are changed.  They can register themselves
        and be closed when that happens.
        """
        self.vulnerable_windows.append(weakref.ref(obj))

    def exit(self, event):
        for process in self.graphs:
            process.terminate()
        self.window.destroy()
        return True

    def xxx(self,
            x=None,
            message1=_('Not implemented!'),
            message2=_('do you really need it?')):
        oops(message1, message2)

    def about(self, action):
        try:
            dialog = gtk.AboutDialog()
            dialog.set_version(__version__)
            dialog.set_website(
                'https://wiki.fysik.dtu.dk/ase/ase/gui/gui.html')
        except AttributeError:
            self.xxx()
        else:
            dialog.run()
            dialog.destroy()

    def get_menu_data(self, show_unit_cell, show_bonds):
        return [
            ('File',
             [('Open', '^O', 'Create a new file', self.open),
              ('New', '^N', 'New ase-gui window',
               lambda widget: os.system('ase-gui &')),
              ('Save', '^S', 'Save current file', lambda x: save_dialog(self)),
              '---',
              ('Quit', '^Q', 'Quit', self.exit)]),

            ('Edit',
             [('Select _all', '', '', self.select_all),
              ('Invert selection', '', '', self.invert_selection),
              ('Select _constrained atoms', '', '',
               self.select_constrained_atoms),
              ('Select _immobile atoms', '^I', '', self.select_immobile_atoms),
              '---',
              ('Copy', '^C',
               'Copy current selection and its orientation to clipboard',
               self.copy_atoms),
              ('_Paste', '^V', 'Insert current clipboard selection',
               self.paste_atoms),
              '---',
              ('Hide selected atoms', '', '', self.hide_selected),
              ('Show selected atoms', '', '', self.show_selected),
              '---',
              ('Modify', '^Y',
               'Change tags, moments and atom types of the selected atoms',
               self.modify_atoms),
              ('_Add atoms', '^A', 'Insert or import atoms and molecules',
               self.add_atoms),
              ('_Delete selected atoms', 'BackSpace',
               'Delete the selected atoms', self.delete_selected_atoms),
              '---',
              ('_First image', 'Home', '', self.step),
              ('_Previous image', 'Page_Up', '', self.step),
              ('_Next image', 'Page_Down', '', self.step),
              ('_Last image', 'End', '', self.step)]),

            ('_View',
             [('Show _unit cell', '^U', '', self.toggle_show_unit_cell,
               show_unit_cell > 0),
              ('Show _axes', '', '', self.toggle_show_axes, True),
              ('Show _bonds', '^B', '', self.toggle_show_bonds, show_bonds),
              ('Show _velocities', '^G', '', self.toggle_show_velocities,
               False),
              ('Show _forces', '^F', '', self.toggle_show_forces, False),
              ('Show _Labels', '', '', self.show_labels,
               ['None', 'Atom _Index', '_Magnetic Moments',
                '_Element Symbol']),
              '---',
              ('Quick Info ...', '', '', self.quick_info_window),
              ('Repeat ...', '', '', self.repeat_window),
              ('Rotate ...', '', '', self.rotate_window),
              ('Colors ...', 'C', '', self.colors_window),
              # TRANSLATORS: verb
              ('Focus', 'F', '', self.focus),
              ('Zoom in', 'plus', '', self.zoom),
              ('Zoom out', 'minus', '', self.zoom),
              ('Change View', '', '', None,
               [('Reset View', 'equal', '', self.reset_view),
                ('\'xy\' Plane', 'z', '', self.set_view),
                ('\'yz\' Plane', 'x', '', self.set_view),
                ('\'zx\' Plane', 'y', '', self.set_view),
                ('\'yx\' Plane', '<alt>z', '', self.set_view),
                ('\'zy\' Plane', '<alt>x', '', self.set_view),
                ('\'xz\' Plane', '<alt>y', '', self.set_view),
                ('\'a2 a3\' Plane', '1', '', self.set_view),
                ('\'a3 a1\' Plane', '2', '', self.set_view),
                ('\'a1 a2\' Plane', '3', '', self.set_view),
                ('\'a3 a2\' Plane', '<alt>1', '', self.set_view),
                ('\'a1 a3\' Plane', '<alt>2', '', self.set_view),
                ('\'a2 a1\' Plane', '<alt>3', '', self.set_view)]),
              ('Settings ...', '', '', self.settings),
              '---',
              ('VMD', '', '', self.external_viewer),
              ('RasMol', '', '', self.external_viewer),
              ('xmakemol', '', '', self.external_viewer),
              ('avogadro', '', '', self.external_viewer)]),

            ('_Tools',
             [('Graphs ...', '', '', self.plot_graphs),
              ('Movie ...', '', '', self.movie),
              ('Expert mode ...', '^E', '', self.execute),
              ('Constraints ...', '', '', self.constraints_window),
              ('Render scene ...', '', '', self.render_window),
              ('_Move atoms', '^M', '', self.toggle_move_mode, False),
              ('_Rotate atoms', '^R', '', self.toggle_rotate_mode, False),
              ('Orien_t atoms', '^T', '', self.toggle_orient_mode, False),
              ('NE_B', '', '', self.NEB),
              ('B_ulk Modulus', '', '', self.bulk_modulus)]),

            # TRANSLATORS: Set up (i.e. build) surfaces, nanoparticles, ...
            ('_Setup',
             [('_Bulk Crystal', '',
               'Create a bulk crystal with arbitrary orientation',
               self.bulk_window),
              ('_Surface slab', '', '"Create the most common surfaces',
               self.surface_window),
              ('_Nanoparticle', '', 'Create a crystalline nanoparticle',
               self.nanoparticle_window),
              ('Nano_tube', '', 'Create a nanotube', self.nanotube_window),
              ('Graphene', '', 'Create a graphene sheet or nanoribbon',
               self.graphene_window)]),

            ('Calculate',
             [('Set _Calculator', '',
               'Set a calculator used in all calculation modules',
               self.calculator_window),
              ('_Energy and Forces', '', 'Calculate energy and forces',
               self.energy_window),
              ('Energy _Minimization', '', 'Minimize the energy',
               self.energy_minimize_window),
              ('Scale system', '', 'Deform system by scaling it',
               self.scaling_window)]),

            ('Help',
             [('_About', '', '', self.about),
              ('Webpage ...', '', '', webpage),
              ('Debug ...', '', '', self.debug)])]


def webpage(widget):
    import webbrowser
    webbrowser.open('https://wiki.fysik.dtu.dk/ase/ase/gui/gui.html')

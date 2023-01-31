"""Holds metadata and methods on Pandora"""

from dataclasses import dataclass

import astropy.units as u
import pandas as pd

from . import PACKAGEDIR
from .irdetector import NIRDetector
from .optics import Optics
from .orbit import Orbit
from .visibledetector import VisibleDetector

import numpy as np
from .utils import get_sky_catalog, get_jitter



@dataclass
class PandoraSat:
    """Holds information and methods for the full Pandora system.

    Args:
        NIRDA (IRDetector): Class of the NIRDA properties
        VISDA (IRDetector): Class of the VISDA properties
        Optics (IRDetector): Class of the Optics properties
        Orbit (IRDetector): Class of the Orbit properties
    """
    Orbit = Orbit()
    Optics = Optics()
    NIRDA = NIRDetector(
        "NIR",
        1.19 * u.arcsec / u.pixel,
        18.0 * u.um / u.pixel,
        2048 * u.pixel,
        512 * u.pixel,
        0.5 * u.electron / u.DN,
        True,
    )
    VISDA = VisibleDetector(
        "Visible",
        0.78 * u.arcsec / u.pixel,
        6.5 * u.um / u.pixel,
        2048 * u.pixel,
        2048 * u.pixel,
        0.5 * u.electron / u.DN,
    )
    targetlist = pd.read_csv(f"{PACKAGEDIR}/data/targets.csv")

    def __repr__(self):
        return "Pandora Sat"

    def get_sky_catalog(self, target_ra, target_dec, magnitude_range=(-3, 16)):
        """Gets the source catalog of an input target
        
        Parameters
        ----------
        target_name : str
            Target name to obtain catalog for.

        Returns
        -------
        sky_catalog: pd.DataFrame   
            Pandas dataframe of all the sources near the target
        """

        # This is fixed for visda, for now
        radius = 0.155 #degrees
        # catalog_data = Catalogs.query_object(target_name, radius=radius, catalog="TIC")
        # target_ra, target_dec = catalog_data[0][['ra', 'dec']].values()

        # Get location and magnitude data
        ra, dec, mag = np.asarray(get_sky_catalog(target_ra, target_dec, radius=radius, magnitude_range=magnitude_range)).T
        k = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(mag)
        ra, dec, mag = ra[k], dec[k], mag[k]
        vis_pix_coords = self.VISDA.wcs(target_ra, target_dec).all_world2pix(np.vstack([ra, dec]).T, 1)
        nir_pix_coords = self.NIRDA.wcs(target_ra, target_dec).all_world2pix(np.vstack([ra, dec]).T, 1)
        
    # we're assuming that Gaia B mag is very close to the Pandora visible magnitude
        vis_counts = np.zeros_like(mag)
        vis_flux = np.zeros_like(mag)
        wav = np.arange(100, 1000) * u.nm
        s = np.trapz(self.VISDA.sensitivity(wav), wav)
        for idx, m in enumerate(mag):
            f = self.VISDA.flux_from_mag(m)
            vis_flux[idx] = f.value
            vis_counts[idx] = (f * s).to(u.electron/u.second).value
        source_catalog = pd.DataFrame(np.vstack([ra, dec, mag, *vis_pix_coords.T, *nir_pix_coords.T, vis_counts, vis_flux]).T,
                                        columns=['ra', 'dec', 'mag', 'vis_x', 'vis_y', 'nir_x', 'nir_y', 'vis_counts', 'vis_flux'])
        return source_catalog


    def get_sky_images(self, source_catalog, wavelength=0.54*u.micron, temperature=10*u.deg_C, nreads=10, nt=40, xjitter_3sigma=2*u.pixel, yjitter_3sigma=2*u.pixel, jitter_timescale=1*u.second, include_noise=True):
        prf = self.VISDA.get_fastPRF(wavelength, temperature)

        # Spacecraft jitter motion
        time, xj, yj = get_jitter(xjitter_3sigma.value, yjitter_3sigma.value, correlation_time=jitter_timescale, nframes=nt * nreads, frame_time=self.VISDA.integration_time)
        science_image = np.zeros(
            (nt, self.VISDA.naxis1.value.astype(int), self.VISDA.naxis2.value.astype(int))
        )


        for jdx in range(nt * nreads):
            for idx, s in source_catalog.iterrows():
                x, y = xj[jdx].value + s.vis_x - self.VISDA.naxis1.value//2, yj[jdx].value + s.vis_y - self.VISDA.naxis2.value//2
                if ((x < -750) | (x > 750) | (y < -750) | (y > 750)):
                    continue
                x1, y1, f = prf(x, y)
                X, Y = np.asarray(np.meshgrid(x1 + self.VISDA.naxis1.value//2, y1 + self.VISDA.naxis2.value//2)).astype(int)
                science_image[jdx//nreads, Y, X] += np.random.poisson(f.T * s.vis_counts)
                
        if include_noise:
            science_image *= u.electron/u.second
            # # background light?
            science_image += self.VISDA.get_background_light_estimate(source_catalog.loc[0, 'ra'], source_catalog.loc[0, 'dec'])

            # time integrate
            science_image *= self.VISDA.integration_time * nreads

            # fieldstop
            science_image *= self.VISDA.fieldstop.astype(float)

            # noise
            for jdx in range(nt):
                noise = np.zeros(self.VISDA.shape, int)

                noise[self.VISDA.fieldstop] = np.random.normal(loc=self.VISDA.bias.value, scale=self.VISDA.read_noise.value * np.sqrt(nreads),
                                        size=self.VISDA.fieldstop.sum()) 
                noise[self.VISDA.fieldstop] += np.random.poisson(lam=(self.VISDA.dark * self.VISDA.integration_time * nreads).value,
                                        size=self.VISDA.fieldstop.sum())

                science_image[jdx] += noise * u.electron
        return time, xj, yj, science_image


    # def get_sky_image(self, source_catalog, wavelength=0.54*u.micron, temperature=10*u.deg_C, nreads=1, include_noise=True):
    #     science_image = np.zeros(
    #         (self.VISDA.naxis1.value.astype(int), self.VISDA.naxis2.value.astype(int))
    #     )
    #     for idx, s in source_catalog.iterrows():
    #         x, y = s.vis_x - self.VISDA.naxis1.value//2, s.vis_y - self.VISDA.naxis2.value//2
    #         if ((x < -650) | (x > 650) | (y < -650) | (y > 650)):
    #             continue
    #         x1, y1, f = self.VISDA.psf.prf(
    #             (x * u.pix, y * u.pix, wavelength, temperature)
    #         )
    #         X, Y = np.asarray(np.meshgrid(x1 + self.VISDA.naxis1.value//2, y1 + self.VISDA.naxis2.value//2)).astype(int)
    #         science_image[Y, X] += f.T * s.vis_counts

        
    #     science_image *= u.electron/u.second
    #     if include_noise:
    #         # # background light?
    #         science_image += self.VISDA.get_background_light_estimate(source_catalog.loc[0, 'ra'], source_catalog.loc[0, 'dec'])

    #         # time integrate
    #         science_image *= self.VISDA.integration_time * nreads

    #         # fieldstop
    #         science_image *= self.VISDA.fieldstop.astype(float)

    #         # noise
    #         noise = np.zeros(self.VISDA.shape, int)

    #         noise[self.VISDA.fieldstop] = np.random.normal(loc=self.VISDA.bias.value, scale=self.VISDA.read_noise.value * np.sqrt(nreads),
    #                                 size=self.VISDA.fieldstop.sum()) 
    #         noise[self.VISDA.fieldstop] += np.random.poisson(lam=(self.VISDA.dark * self.VISDA.integration_time * nreads).value,
    #                                 size=self.VISDA.fieldstop.sum())

    #         science_image += noise * u.electron
    #     return science_image

/** The four registration-mark corners that go inside any `.blueprint` box.
 *  Replaces the hand-repeated <i className="corner tl" /> quartet -- it appeared
 *  ~15 times across the pages, on both divs and buttons, which is why this is a
 *  bare fragment rather than a wrapper element. */
export function Corners() {
  return (
    <>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
    </>
  )
}

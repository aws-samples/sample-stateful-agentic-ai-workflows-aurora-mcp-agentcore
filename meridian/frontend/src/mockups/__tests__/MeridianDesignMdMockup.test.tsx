import { fireEvent, render, screen } from '@testing-library/react'
import { MeridianDesignMdMockup } from '../MeridianDesignMdMockup'

describe('MeridianDesignMdMockup', () => {
  it('switches into recovery and updates the selected plan', () => {
    render(<MeridianDesignMdMockup />)

    fireEvent.click(screen.getByRole('tab', { name: 'Recovery' }))

    expect(
      screen.getByRole('heading', { name: 'Alex’s JFK to Tokyo recovery' }),
    ).toBeInTheDocument()

    const directOption = screen.getByRole('button', {
      name: /Fewer changes Wait for the next nonstop/,
    })
    fireEvent.click(directOption)

    expect(directOption).toHaveAttribute('aria-pressed', 'true')
    expect(
      screen.getByRole('heading', { name: 'Wait for the next nonstop' }),
    ).toBeInTheDocument()
  })

  it('promotes a supporting discovery recommendation', () => {
    render(<MeridianDesignMdMockup />)

    fireEvent.click(
      screen.getAllByRole('button', { name: /Feature this trip/i })[0],
    )

    expect(
      screen.getByRole('heading', { name: 'Tokyo, quietly' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /View itinerary/i })).toBeInTheDocument()
  })
})
